"""What Jira notifications say, from two snapshots and the graph."""

from typing import cast

from conftest import NOW, events_between, issue, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.notifications import Event
from move_forward.page import anchor
from move_forward.plugin import Previous
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.jira.changes import KINDS
from move_forward.plugins.jira.models import JiraIssue, JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin


def _events(before: list[JiraIssue], after: list[JiraIssue]) -> list[Event]:
    return events_between(
        "jira", {"jira": JiraSnapshot(issues=before)}, {"jira": JiraSnapshot(issues=after)}
    )


def _kinds(before: list[JiraIssue], after: list[JiraIssue]) -> list[tuple[str, str]]:
    return [(e.kind, e.text) for e in _events(before, after)]


def test_every_kind_the_plugin_offers_is_listed() -> None:
    assert JiraPlugin.notification_kinds == KINDS


def test_a_new_ticket_is_assigned_and_says_nothing_else() -> None:
    new = issue("APP-2", priority="High")
    assert _kinds([issue()], [issue(), new]) == [("assigned", "assigned to you")]


def test_a_raised_priority_is_said_but_a_lowered_or_unknown_one_is_not() -> None:
    assert _kinds([issue(priority="Low")], [issue(priority="High")]) == [
        ("priority_raised", "priority is now High")
    ]
    assert _kinds([issue(priority="High")], [issue(priority="Low")]) == []
    assert _kinds([issue(priority="Low")], [issue(priority="Urgent")]) == []
    assert _kinds([issue(priority=None)], [issue(priority="High")]) == []


def test_the_last_blocker_finishing_is_said() -> None:
    assert _kinds([issue(open_blockers=["APP-9"])], [issue()]) == [
        ("unblocked", "no longer blocked")
    ]
    assert _kinds([issue(open_blockers=["APP-9", "APP-8"])], [issue(open_blockers=["APP-8"])]) == []


def test_an_unblocked_tickets_event_is_about_its_key_and_opens_at_it() -> None:
    [event] = _events([issue(open_blockers=["APP-9"])], [issue()])
    assert (event.thing, event.title, event.anchor) == (
        "APP-1",
        "APP-1 Do the thing",
        anchor("ticket", "https://jira/browse/APP-1"),
    )


class _Refreshes:
    """What core finds of jira's events over what's stored, as successive refreshes do:
    each against what the one before saw (the first against nothing)."""

    def __init__(self, pair: tuple[Config, Database]):
        self.pair = pair
        self.previous: tuple[Graph, JiraSnapshot | None] | None = None

    def closeable(self) -> list[str]:
        config, db = self.pair
        enabled = registry.enabled(config, db, NOW)
        graph, snapshots = refresh.stored_graph(db, enabled)
        jira = next(e for e in enabled if e.name == "jira")
        now = cast(JiraSnapshot | None, snapshots["jira"])
        before_graph, before = self.previous or (graph, None)
        self.previous = (graph, now)
        events = refresh.events_of(jira, Previous(snapshot=before, graph=before_graph), now, graph)
        return [e.thing for e in events if e.kind == "closeable"]


def test_a_ticket_whose_prs_merge_is_closeable_once_and_not_the_first_time() -> None:
    tickets = JiraSnapshot(issues=[issue("APP-1", status="In Review")])
    open_pr = pr(1)
    config, db = stored(github=snapshot(mine=[open_pr]), jira=tickets)
    refreshes = _Refreshes((config, db))
    assert refreshes.closeable() == []  # first: nothing to compare with
    assert refreshes.closeable() == []
    PrRepository(db).replace(snapshot(merged_recent=[pr(1, state="MERGED", merged_at=NOW)]))
    assert refreshes.closeable() == ["APP-1"]
    assert refreshes.closeable() == []  # said once


def test_a_ticket_already_closeable_the_first_time_is_not_said() -> None:
    tickets = JiraSnapshot(issues=[issue("APP-1", status="In Review")])
    merged = snapshot(merged_recent=[pr(1, state="MERGED", merged_at=NOW)])
    pair = stored(github=merged, jira=tickets)
    assert _Refreshes(pair).closeable() == []
