"""Core's Act now: ranking the plugins' nominations into a short list, and saying why."""

from datetime import datetime, timedelta

from conftest import NOW

from move_forward import act_now
from move_forward.act_now import Feedback, FeedbackKey, Nomination
from move_forward.db import Database
from move_forward.graph import Graph, Ref
from move_forward.page import Item, Panel, Signals
from move_forward.plugin import Context
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import KvRepository


def _item(title: str, subject: str | None = None, **signals: object) -> Item:
    return Item(
        title=title,
        subjects=[Ref(kind="pr", id=subject)] if subject else [],
        signals=Signals.model_validate(signals),
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


def test_finishing_beats_quick_beats_unknown_and_blocked_is_out() -> None:
    ranked = _ranked(
        [
            Nomination("github", _item("unknown")),
            Nomination("github", _item("quick", effort="minutes")),
            Nomination("jira", _item("closes", closes="APP-1", verb="Close")),
            Nomination("github", _item("blocked", effort="minutes", closes="x", blocked=True)),
            Nomination("slack", _item("waited on", effort="hour", waiting=["alice"])),
        ]
    )
    assert _titles(ranked) == ["Close: closes", "waited on", "quick", "unknown"]
    assert ranked.items[1].details == ["why: about an hour, alice waiting"]
    assert ranked.items[0].details == ["why: closes APP-1"]
    assert [i.rank for i in ranked.items] == [7, 6, 5, 4]  # the panel keeps its order


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


def test_by_default_a_plugin_nominates_its_act_now_panels_items_with_their_signals() -> None:
    own = Item(title="own", signals=Signals(verb="Merge"))
    plain = Item(title="plain")
    panels = [
        Panel(
            key="a",
            title="A",
            description="",
            items=[own, plain],
            act_now=True,
            signals=Signals(verb="Review", effort="minutes"),
        ),
        Panel(key="b", title="B", description="", items=[Item(title="not nominated")]),
    ]
    db = Database(":memory:")
    ctx = Context(
        config=JiraConfig(site="https://j"),
        email="me@x",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "jira"),
    )
    nominated = JiraPlugin().act_now(ctx, None, Graph(), panels)
    assert [(i.title, i.signals) for i in nominated] == [
        ("own", Signals(verb="Merge")),
        ("plain", Signals(verb="Review", effort="minutes")),
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


def test_dismissed_and_snoozed_items_stay_away_until_they_change_or_wake() -> None:
    review = _item("PR 1", "u1", verb="Review")
    other = _item("PR 2", "u2")
    nominations = [Nomination("github", review), Nomination("github", other)]
    dismissed = _feedback("dismiss", review)
    assert _titles(_ranked(nominations, feedback=dismissed, now=NOW)) == ["PR 2"]
    changed = review.model_copy(update={"badges": ["+400 lines"]})
    again = [Nomination("github", changed), Nomination("github", other)]
    assert _titles(_ranked(again, feedback=dismissed, now=NOW)) == ["Review: PR 1", "PR 2"]
    snoozed = _feedback("snooze", review, until=NOW + timedelta(hours=1))
    assert _titles(_ranked(nominations, feedback=snoozed, now=NOW)) == ["PR 2"]
    later = NOW + timedelta(hours=2)
    assert _titles(_ranked(nominations, feedback=snoozed, now=later))[0] == "Review: PR 1"


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
    assert [a.label for a in ranked.items[0].actions] == ["Snooze", "Dismiss", "Unpin"]
    assert [a.label for a in ranked.items[1].actions] == ["Snooze", "Dismiss", "Pin"]
    assert ranked.items[1].actions[0].fields == {
        "kind": "pr",
        "id": "u2",
        "verb": "",
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
