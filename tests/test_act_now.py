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


def _titles(panel: Panel) -> list[str]:
    return [i.title for i in panel.items]


def test_finishing_beats_quick_beats_unknown_and_blocked_is_out() -> None:
    ranked = act_now.panel(
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


def test_one_item_per_subject_with_the_others_mentioned() -> None:
    ranked = act_now.panel(
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
    ranked = act_now.panel([*many, Nomination("jira", _item("j"))], config)
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
    assert _titles(act_now.panel(nominations, feedback=dismissed, now=NOW)) == ["PR 2"]
    changed = review.model_copy(update={"badges": ["+400 lines"]})
    again = [Nomination("github", changed), Nomination("github", other)]
    assert _titles(act_now.panel(again, feedback=dismissed, now=NOW)) == ["Review: PR 1", "PR 2"]
    snoozed = _feedback("snooze", review, until=NOW + timedelta(hours=1))
    assert _titles(act_now.panel(nominations, feedback=snoozed, now=NOW)) == ["PR 2"]
    later = NOW + timedelta(hours=2)
    assert _titles(act_now.panel(nominations, feedback=snoozed, now=later))[0] == "Review: PR 1"


def test_a_pinned_item_comes_first_and_says_so() -> None:
    plain = _item("PR 1", "u1")
    quick = _item("PR 2", "u2", effort="minutes")
    ranked = act_now.panel(
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
    nothing = act_now.panel([Nomination("notes", Item(title="about nothing"))])
    assert nothing.items[0].actions == []  # no subject to keep feedback by
