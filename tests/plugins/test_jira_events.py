"""What Jira notifications say, from two snapshots and the graph."""

from conftest import NOW, issue, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.jira.changes import KINDS, changes
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraComment, JiraIssue, JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.jira.repository import IssueRepository

CONFIG = JiraConfig(site="https://jira")


def _kinds(before: list[JiraIssue], after: list[JiraIssue]) -> list[tuple[str, str]]:
    events = changes(CONFIG, JiraSnapshot(issues=before), JiraSnapshot(issues=after))
    return [(e.kind, e.text) for e in events]


def _comment(id: str, author: str = "Alice", **fields: bool) -> JiraComment:
    return JiraComment(id=id, author=author, created=NOW, **fields)


def test_every_kind_the_plugin_offers_is_listed() -> None:
    assert JiraPlugin.notification_kinds == KINDS


def test_a_new_ticket_is_assigned_and_says_nothing_else() -> None:
    new = issue("APP-2", priority="High", comments=[_comment("1")])
    assert _kinds([issue()], [issue(), new]) == [("assigned", "assigned to you")]


def test_new_comments_are_said_but_not_my_own_or_old_ones() -> None:
    old = issue(comments=[_comment("1")])
    new = issue(comments=[_comment("1"), _comment("2", "Bob"), _comment("3", "Me", mine=True)])
    assert _kinds([old], [new]) == [("comment_added", "Bob commented")]


def test_a_mention_is_said_as_a_mention_not_a_comment() -> None:
    new = issue(comments=[_comment("1", "Bob", mentions_me=True)])
    assert _kinds([issue()], [new]) == [("mentioned", "Bob mentioned you")]


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


def test_comments_are_kept_with_the_snapshot() -> None:
    db = stored(jira=JiraSnapshot(issues=[]))[1]
    store = IssueRepository(db)
    with_comments = JiraSnapshot(issues=[issue(comments=[_comment("1", mentions_me=True)])])
    store.replace(with_comments)
    assert store.load() == with_comments


def _closeable_events(pair: tuple[Config, Database]) -> list[str]:
    config, db = pair
    enabled = registry.enabled(config, db, NOW)
    graph, _ = refresh.stored_graph(db, enabled)
    plugin = next(e for e in enabled if e.name == "jira")
    return [e.thing for e in JiraPlugin().graph_events(plugin.ctx, graph)]


def test_a_ticket_whose_prs_merge_is_closeable_once_and_not_the_first_time() -> None:
    tickets = JiraSnapshot(issues=[issue("APP-1", status="In Review")])
    open_pr = pr(1)
    config, db = stored(github=snapshot(mine=[open_pr]), jira=tickets)
    assert _closeable_events((config, db)) == []  # first: keeps what's there
    assert _closeable_events((config, db)) == []
    PrRepository(db).replace(snapshot(merged_recent=[pr(1, state="MERGED", merged_at=NOW)]))
    assert _closeable_events((config, db)) == ["APP-1"]
    assert _closeable_events((config, db)) == []  # said once


def test_a_ticket_already_closeable_the_first_time_is_not_said() -> None:
    tickets = JiraSnapshot(issues=[issue("APP-1", status="In Review")])
    merged = snapshot(merged_recent=[pr(1, state="MERGED", merged_at=NOW)])
    pair = stored(github=merged, jira=tickets)
    assert _closeable_events(pair) == []
