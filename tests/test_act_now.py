"""Core's Act now: ranking the plugins' nominations into a short list, and saying why."""

import hashlib
import json
from datetime import datetime, timedelta

from conftest import NOW

from move_forward import act_now
from move_forward import panels as panels_module
from move_forward.act_now import Feedback, FeedbackKey, Nomination
from move_forward.graph import Ref
from move_forward.page import Action, Item, Panel, Signals


def _item(title: str, subject: str | None = None, **signals: object) -> Item:
    return Item(
        title=title,
        subjects=[Ref(kind="pull_request", id=subject)] if subject else [],
        signals=Signals.model_validate({"task": "do_it", **signals}),
    )


def _ranked(
    nominations: list[Nomination],
    config: act_now.ActNowConfig | None = None,
    feedback: dict[FeedbackKey, Feedback] | None = None,
    now: datetime | None = None,
) -> Panel:
    """Act now's items across its panels, in their order overall."""
    panels = act_now.panels(nominations, config, feedback, now)
    items = sorted((i for p in panels for i in p.items), key=lambda i: -i.rank)
    return Panel(key="all", title="", description="", items=items)


def _titles(panel: Panel) -> list[str]:
    return [i.title for i in panel.items]


def test_finishing_beats_quick_beats_unknown() -> None:
    ranked = _ranked(
        [
            Nomination("github", _item("unknown")),
            Nomination("github", _item("quick", effort="minutes")),
            Nomination("jira", _item("closes", closes="APP-1", verb="Close")),
            Nomination("slack", _item("waited on", effort="hour", waiting=["alice"])),
        ]
    )
    assert _titles(ranked) == ["Close: closes", "waited on", "quick", "unknown"]
    assert ranked.items[1].details == ["why: takes about an hour, alice waiting"]
    assert ranked.items[0].details == ["why: closes APP-1"]
    assert [i.rank for i in ranked.items] == [7, 6, 5, 4]  # the panel keeps its order


def _blocked_and_open(
    touched: datetime | None, config: act_now.ActNowConfig | None = None
) -> Panel:
    """A blocked and an unblocked item that are otherwise the same, the blocked one first in
    line; the blocked one last, with `why` saying so, unless it has gone stale."""
    blocked = _item("blocked", effort="minutes", blocked=True, touched=touched)
    return _ranked(
        [Nomination("jira", blocked), Nomination("github", _item("open", effort="minutes"))],
        config,
        now=NOW,
    )


def test_a_blocked_item_ranks_below_the_same_item_unblocked() -> None:
    ranked = _blocked_and_open(NOW - timedelta(days=1))
    assert _titles(ranked) == ["open", "blocked"]
    assert ranked.items[1].details == ["why: blocked, quick"]


def test_a_blocked_item_untouched_for_the_stale_days_ranks_as_if_unblocked() -> None:
    ranked = _blocked_and_open(NOW - timedelta(days=4))
    assert _titles(ranked) == ["blocked", "open"]  # tied, so in nomination order
    assert ranked.items[0].details == ["why: blocked, untouched 4d, quick"]


def test_a_blocked_item_never_goes_stale_without_a_touch_or_with_stale_days_at_zero() -> None:
    assert _titles(_blocked_and_open(None)) == ["open", "blocked"]
    never = act_now.ActNowConfig(blocked_stale_days=0)
    assert _titles(_blocked_and_open(NOW - timedelta(days=30), never)) == ["open", "blocked"]


def test_importance_time_waited_and_a_nearing_due_date_raise_an_item() -> None:
    ranked = _ranked(
        [
            Nomination("github", _item("quick", effort="minutes")),
            Nomination("jira", _item("critical", importance=2)),
            Nomination(
                "github",
                _item("waited a week", waiting=["bob"], waiting_since=NOW - timedelta(days=7)),
            ),
            Nomination(
                "github",
                _item("waited an hour", waiting=["al"], waiting_since=NOW - timedelta(hours=1)),
            ),
            Nomination("jira", _item("overdue", due=NOW - timedelta(days=1))),
            Nomination("jira", _item("due in 3 days", due=NOW + timedelta(days=3))),
            Nomination("jira", _item("due in a month", due=NOW + timedelta(days=30))),
        ],
        act_now.ActNowConfig(per_plugin=7),
        now=NOW,
    )
    # critical 6; waited a week 2 + log2(8) = 5; overdue 4; waited an hour ~2.06;
    # due in 3 days 4 * 4/7 ~ 2.3; quick 2; a month away 0.
    assert _titles(ranked) == [
        "critical",
        "waited a week",
        "overdue",
        "due in 3 days",
        "waited an hour",
        "quick",
        "due in a month",
    ]
    why = {i.title: i.details for i in ranked.items}
    assert why["critical"] == ["why: critical"]
    assert why["waited a week"] == ["why: bob waiting 7d"]
    assert why["overdue"] == ["why: overdue by 1d"] and why["due in 3 days"] == ["why: due in 3d"]
    assert why["due in a month"] == []  # too far off to count, or to mention


def test_the_due_warning_days_say_how_far_ahead_a_due_date_counts() -> None:
    soon = Nomination("jira", _item("due in 3 days", due=NOW + timedelta(days=3)))
    default = _ranked([soon], act_now.ActNowConfig(), now=NOW)
    assert default.items[0].details == ["why: due in 3d"]
    narrow = _ranked([soon], act_now.ActNowConfig(due_warning_days=2), now=NOW)
    assert narrow.items[0].details == []  # three days out is beyond a two-day warning
    wide = act_now.ActNowConfig(due_warning_days=30)
    assert _ranked([soon], wide, now=NOW).items[0].details == ["why: due in 3d"]


def test_without_now_time_does_not_count() -> None:
    since = NOW - timedelta(days=30)
    assert act_now.score(Signals(waiting=["a"], waiting_since=since), act_now.Weights()) == 2
    assert act_now.why(Signals(waiting=["a"], waiting_since=since, due=since)) == "a waiting"


def test_one_item_per_subject_with_the_others_mentioned() -> None:
    ranked = _ranked(
        [
            Nomination("github", _item("PR 1", "u1", effort="hour", verb="Fix CI")),
            Nomination("github", _item("PR 1", "u1", effort="minutes", verb="Reply")),
            Nomination("github", _item("PR 2", "u2")),
        ]
    )
    assert _titles(ranked) == ["Reply: PR 1", "PR 2"]
    assert ranked.items[0].details == ["why: quick", "also: Fix CI"]


def test_no_plugin_fills_more_than_its_share_and_the_list_is_short() -> None:
    many = [Nomination("github", _item(f"g{n}", effort="minutes")) for n in range(5)]
    config = act_now.ActNowConfig(size=4, per_plugin=3)
    ranked = _ranked([*many, Nomination("jira", _item("j"))], config)
    assert _titles(ranked) == ["g0", "g1", "g2", "j"]


def test_an_act_now_panels_items_are_candidates_with_their_signals_but_as_they_say() -> None:
    review = Signals(verb="Review", task="review", effort="minutes")
    panels = [
        Panel(
            key="a",
            title="A",
            description="",
            items=[
                Item(title="own", signals=Signals(verb="Merge", task="merge")),
                Item(title="plain"),
                Item(title="draft", deferred=True),
                Item(title="draft, but", deferred=True, act_now=True),
                Item(title="not this one", act_now=False),
            ],
            act_now=True,
            signals=review,
        ),
        Panel(
            key="b",
            title="B",
            description="",
            items=[
                Item(title="not nominated", signals=review),
                Item(title="due soon", signals=review, act_now=True),
                Item(title="no signals", act_now=True),
            ],
        ),
    ]
    assert [(i.title, i.signals) for i in panels_module.candidates(panels)] == [
        ("own", Signals(verb="Merge", task="merge")),
        ("plain", review),
        ("draft, but", review),
        ("due soon", review),
    ]


def _feedback(
    action: str, item: Item, until: datetime | None = None
) -> dict[FeedbackKey, Feedback]:
    nomination = Nomination("github", item)
    assert nomination.key is not None
    return {
        nomination.key: Feedback.model_validate(
            {"action": action, "fingerprint": nomination.fingerprint, "until": until}
        )
    }


def test_snoozed_items_stay_away_until_they_change_or_wake() -> None:
    review = _item("PR 1", "u1", verb="Review")
    other = _item("PR 2", "u2")
    nominations = [Nomination("github", review), Nomination("github", other)]
    until_changed = _feedback("snooze", review)
    years_on = NOW + timedelta(days=1000)
    assert _titles(_ranked(nominations, feedback=until_changed, now=years_on)) == ["PR 2"]
    changed = review.model_copy(update={"badges": ["+400 lines"]})
    again = [Nomination("github", changed), Nomination("github", other)]
    assert _titles(_ranked(again, feedback=until_changed, now=NOW)) == ["Review: PR 1", "PR 2"]
    snoozed = _feedback("snooze", review, until=NOW + timedelta(hours=1))
    assert _titles(_ranked(nominations, feedback=snoozed, now=NOW)) == ["PR 2"]
    later = NOW + timedelta(hours=2)
    assert _titles(_ranked(nominations, feedback=snoozed, now=later))[0] == "Review: PR 1"


def test_rewording_an_items_verb_keeps_what_i_said_of_it() -> None:
    move = _item("APP-1", "u1", verb="Move to In Review", task="transition")
    snoozed = _feedback("snooze", move, until=NOW + timedelta(hours=1))
    renamed = move.model_copy(
        update={"signals": Signals(verb="Move to Code Review", task="transition")}
    )
    assert _titles(_ranked([Nomination("jira", renamed)], feedback=snoozed, now=NOW)) == []


def test_a_pinned_item_comes_first_and_says_so() -> None:
    plain = _item("PR 1", "u1")
    quick = _item("PR 2", "u2", effort="minutes")
    ranked = _ranked(
        [Nomination("github", plain), Nomination("github", quick)],
        feedback=_feedback("pin", plain),
        now=NOW,
    )
    assert _titles(ranked) == ["PR 1", "PR 2"]
    assert ranked.items[0].details == ["why: pinned"]
    assert [a.label for a in ranked.items[0].actions][:2] == ["Snooze", "Unpin"]
    assert [a.label for a in ranked.items[1].actions][:2] == ["Snooze", "Pin"]
    assert ranked.items[1].actions[0].choices == {
        "for a while": "",
        "until it changes": "changed",
    }
    assert ranked.items[1].actions[0].fields == {
        "kind": "pull_request",
        "id": "u2",
        "task": "do_it",
        "fingerprint": Nomination("github", quick).fingerprint,
    }
    nothing = _ranked([Nomination("notes", Item(title="about nothing"))])
    assert nothing.items[0].actions == []  # no subject to keep feedback by


def test_act_now_is_split_by_plugin_in_its_best_items_order_and_numbered_overall() -> None:
    panels = act_now.panels(
        [
            Nomination("github", _item("review", effort="minutes")),
            Nomination("jira", _item("close", closes="APP-1")),
            Nomination("github", _item("unknown")),
        ],
        titles={"github": "GitHub", "jira": "Jira"},
    )
    assert [(p.key, p.title) for p in panels] == [("jira", "Jira"), ("github", "GitHub")]
    assert [[(i.title, i.badges[0]) for i in p.items] for p in panels] == [
        [("close", "#1")],
        [("review", "#2"), ("unknown", "#3")],
    ]
    assert [p.key for p in act_now.panels([])] == ["now"]  # one empty panel says so


def test_an_item_about_a_unit_of_work_joins_the_best_item_about_something_in_it() -> None:
    ticket, entry = Ref(kind="ticket", id="APP-1"), Ref(kind="notes.entry", id="9")
    units = {
        Ref(kind="pull_request", id="1"): [ticket],
        Ref(kind="pull_request", id="2"): [ticket],
        ticket: [ticket],
        entry: [entry],
    }
    move = Item(
        title="APP-1 Do it",
        url="https://jira/APP-1",
        subjects=[ticket],
        signals=Signals(verb="Move to In Review", task="move_to_in_review", effort="minutes"),
        actions=[Action(name="jira/transition", label="Move", fields={"key": "APP-1"})],
    )
    todo = Item(title="Write it up", subjects=[entry], signals=Signals(effort="minutes"))
    panels = act_now.panels(
        [
            Nomination(
                "github", _item("PR 1", "1", verb="Reply", effort="minutes", waiting=["al"])
            ),
            Nomination("github", _item("PR 2", "2", verb="Fix CI", effort="hour")),
            Nomination("jira", move),
            Nomination("notes", todo),
        ],
        now=NOW,
        units=lambda ref: units.get(ref, []),
    )
    items = sorted((i for p in panels for i in p.items), key=lambda i: -i.rank)
    assert [i.title for i in items] == ["Reply: PR 1", "Write it up", "Fix CI: PR 2"]
    joined = items[0]
    assert joined.details == ["why: quick, al waiting", "then: Move to In Review: APP-1 Do it"]
    assert [a.label for a in joined.actions][:3] == ["Move", "Snooze", "Pin"]
    assert [link.url for link in joined.links] == ["https://jira/APP-1"]
    also = json.loads(joined.actions[1].fields["also"])
    assert also == [["ticket", "APP-1", "move_to_in_review", Nomination("jira", move).fingerprint]]
    assert [p.key for p in panels] == ["github", "notes"]  # the ticket's item went with its PR


def test_equal_scores_keep_the_order_they_were_nominated_in_whatever_their_rank() -> None:
    low = _item("first", "u1").model_copy(update={"rank": 1})
    high = _item("second", "u2").model_copy(update={"rank": 3000})  # a panel's own order
    assert _titles(_ranked([Nomination("jira", low), Nomination("github", high)])) == [
        "first",
        "second",
    ]


def _close_out(*todos: str) -> Item:
    return Item(
        title="entry",
        subjects=[Ref(kind="notes.entry", id="entry")],
        signals=Signals(verb="Finish them", task="close_out", effort="minutes"),
        parts=[Item(title=todo, subjects=[Ref(kind="notes.todo", id=todo)]) for todo in todos],
    )


def test_an_item_with_several_parts_keeps_them() -> None:
    [panel] = act_now.panels([Nomination("notes", _close_out("one", "two"))])
    assert [part.title for part in panel.items[0].parts] == ["one", "two"]


def test_an_item_with_one_part_has_its_buttons_itself_so_lists_none() -> None:
    [panel] = act_now.panels([Nomination("notes", _close_out("one"))])
    assert panel.items[0].parts == []


def test_a_joined_items_parts_are_listed_with_its_host() -> None:
    entry = Ref(kind="notes.entry", id="entry")
    pr = Ref(kind="pull_request", id="u")
    host = Item(
        title="PR", subjects=[pr], signals=Signals(verb="Reply", task="reply", waiting=["a"])
    )

    def units(ref: Ref) -> list[Ref]:
        return [entry]

    [panel] = act_now.panels(
        [Nomination("github", host), Nomination("notes", _close_out("one", "two"))], units=units
    )
    assert [part.title for part in panel.items[0].parts] == ["one", "two"]


def test_a_change_in_an_items_parts_makes_it_worth_another_look() -> None:
    before = Nomination("notes", _close_out("one", "two")).fingerprint
    assert Nomination("notes", _close_out("one", "three")).fingerprint != before


def test_an_item_without_parts_keeps_its_fingerprint() -> None:
    item = Item(title="t", badges=["b"], details=["d"])
    seen = json.dumps(["t", ["b"], ["d"]])
    assert Nomination("x", item).fingerprint == hashlib.sha256(seen.encode()).hexdigest()[:16]


def _task(task: str, *labels: str, **signals: object) -> Nomination:
    return Nomination(
        "jira",
        Item(
            title="T-1",
            subjects=[Ref(kind="ticket", id="T-1")],
            actions=[Action(name=f"jira/{label}", label=label, fields={}) for label in labels],
            signals=Signals.model_validate({"verb": task, "task": task, **signals}),
        ),
    )


def test_what_else_is_due_on_a_subject_keeps_its_buttons() -> None:
    [panel] = act_now.panels([_task("look", importance=2), _task("move", "Move to Review")])
    [item] = panel.items
    assert item.title == "look: T-1"
    assert "also: move" in item.details
    assert "Move to Review" in [a.label for a in item.actions]


def test_a_button_two_tasks_offer_shows_once() -> None:
    [panel] = act_now.panels([_task("look", "Open", importance=2), _task("move", "Open")])
    assert [a.label for a in panel.items[0].actions].count("Open") == 1


def test_setting_an_item_aside_covers_what_else_is_due_on_its_subject() -> None:
    top, other = _task("look", importance=2), _task("move")
    [panel] = act_now.panels([top, other])
    snooze = next(a for a in panel.items[0].actions if a.label == "Snooze")
    assert json.loads(snooze.fields["also"]) == [[*(other.key or ()), other.fingerprint]]
    until = NOW + timedelta(days=1)
    feedback = {
        n.key: Feedback(action="snooze", fingerprint=n.fingerprint, until=until)
        for n in (top, other)
        if n.key
    }
    assert act_now.panels([top, other], feedback=feedback, now=NOW)[0].items == []
