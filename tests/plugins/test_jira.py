from datetime import UTC, datetime

import pytest
from legacy import save_json_snapshot

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.jira import client as jira
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraIssue, JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.jira.repository import MIGRATIONS, IssueRepository
from move_forward.repositories import KvRepository

RAW: dict[str, object] = {
    "key": "APP-1",
    "id": "12345",
    "fields": {
        "summary": "Fix it",
        "status": {
            "name": "In Progress",
            "statusCategory": {"key": "indeterminate", "colorName": "yellow"},
        },
        "issuetype": {"name": "Bug"},
        "labels": ["needs-attention"],
        "priority": {"name": "P2"},
        "created": "2026-10-01T10:00:00.000+0000",
        "updated": "2026-10-20T10:00:00.000+0000",
        "parent": {"key": "APP-0"},
        "issuelinks": [
            {
                "type": {"inward": "is blocked by", "outward": "blocks"},
                "inwardIssue": {
                    "key": "APP-2",
                    "fields": {"status": {"statusCategory": {"key": "new"}}},
                },
            },
            {
                "type": {"inward": "is blocked by", "outward": "blocks"},
                "inwardIssue": {
                    "key": "APP-3",
                    "fields": {"status": {"statusCategory": {"key": "done"}}},
                },
            },
            {
                "type": {"inward": "relates to", "outward": "relates to"},
                "outwardIssue": {"key": "APP-4"},
            },
        ],
        "customfield_99999": "ignored",
    },
}


def test_to_issue() -> None:
    issue = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://example.atlassian.net/")
    assert issue.key == "APP-1"
    assert issue.status == "In Progress" and issue.status_category == "indeterminate"
    assert issue.type == "Bug" and issue.priority == "P2"
    assert issue.parent == "APP-0"
    assert issue.open_blockers == ["APP-2"]
    assert issue.created == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert issue.url == "https://example.atlassian.net/browse/APP-1"


def test_minimal_issue() -> None:
    raw = {
        "key": "X-1",
        "fields": {"created": "2026-10-01T00:00:00Z", "updated": "2026-10-01T00:00:00Z"},
    }
    issue = jira.to_issue(jira.RawIssue.model_validate(raw), "https://j")
    assert issue.summary == "" and issue.priority is None and issue.open_blockers == []


def test_tickets_are_queried_by_key_and_nothing_else_reaches_the_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_search(_config: object, _email: str, jql: str, _token: str) -> list[JiraIssue]:
        seen.append(jql)
        return []

    monkeypatch.setattr(jira, "search", fake_search)
    config = JiraConfig(site="https://j")
    jira.tickets(config, "me@x", ["APP-1", "OPS_2-7", 'X-1) OR project = "SECRET', "app-3"], "t")
    assert seen == ["key in (APP-1, OPS_2-7)"]
    assert jira.tickets(config, "me@x", ["not a key"], "t") == [] and len(seen) == 1


def test_tickets_are_fetched_open_or_resolved_and_their_projects_remembered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j").model_copy(
        update={"resolved": datetime(2026, 6, 1, tzinfo=UTC)}
    )
    open_one = resolved.model_copy(update={"key": "OPS-2", "resolved": None})

    def tickets(config: JiraConfig, email: str, keys: list[str], api_token: str) -> list[JiraIssue]:
        assert api_token == "t" and keys == ["APP-1", "OPS-2"]
        return [resolved, open_one]

    def token(config: JiraConfig, email: str) -> str:
        return "t"

    monkeypatch.setattr(jira, "token", token)
    monkeypatch.setattr(jira, "tickets", tickets)
    db = Database(":memory:")
    db.migrate("jira", MIGRATIONS)
    ctx = _ctx(db)
    fetched = JiraPlugin().fetch(ctx, "ticket", ["APP-1", "OPS-2", "not a key"])
    assert [(e.ref.id, e.url, e.attrs) for e in fetched] == [
        ("APP-1", resolved.url, {"resolved_at": "2026-06-01T00:00:00+00:00"}),
        ("OPS-2", open_one.url, {"resolved_at": None}),
    ]
    assert IssueRepository(db).projects() == ["APP", "OPS"]
    assert JiraPlugin().fetch(ctx, "pr", ["x"]) == []
    assert JiraPlugin().fetch(ctx, "ticket", ["not a key"]) == []  # without asking Jira


def test_tickets_move_from_the_json_snapshot_and_round_trip() -> None:
    db = Database(":memory:")
    labelled = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    bare = labelled.model_copy(update={"key": "APP-3", "labels": [], "open_blockers": []})
    save_json_snapshot(db, "jira", JiraSnapshot(issues=[labelled, bare]))
    db.migrate("jira", MIGRATIONS)
    repo = IssueRepository(db)
    assert repo.load() == JiraSnapshot(issues=[labelled, bare])
    repo.replace(JiraSnapshot(issues=[bare]))
    assert repo.load() == JiraSnapshot(issues=[bare])
    assert db.query("SELECT * FROM jira_issue_labels") == []  # gone with their tickets


def test_a_ticket_a_search_found_twice_is_kept_once() -> None:
    db = Database(":memory:")
    db.migrate("jira", MIGRATIONS)
    first = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    other = first.model_copy(update={"key": "APP-3"})
    repo = IssueRepository(db)
    repo.replace(JiraSnapshot(issues=[first, other, first.model_copy(update={"summary": "x"})]))
    assert [(i.key, i.summary) for i in repo.load().issues] == [
        (first.key, first.summary),
        ("APP-3", first.summary),
    ]


def _ctx(db: Database) -> Context[JiraConfig]:
    return Context(
        config=JiraConfig(site="https://j"),
        email="me@x",
        teams=[],
        prune_days=90,
        now=datetime(2026, 10, 30, tzinfo=UTC),
        db=db,
        kv=KvRepository(db, "jira"),
    )


def test_ticket_keys_are_only_those_of_projects_my_tickets_have_been_in() -> None:
    db = Database(":memory:")
    db.migrate("jira", MIGRATIONS)
    plugin = JiraPlugin()
    assert plugin.linking_patterns(_ctx(db)) == {"ticket": None}  # nothing seen yet
    first = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    repo = IssueRepository(db)
    repo.replace(JiraSnapshot(issues=[first.model_copy(update={"key": "OPS_2-7"})]))
    repo.replace(JiraSnapshot(issues=[first]))  # OPS_2's last ticket is gone: still known
    assert repo.projects() == ["APP", "OPS_2"]
    graph = Graph(linking=plugin.linking_patterns(_ctx(db)))
    found = graph.named("APP-1, OPS_2-9, UTF-8, SHA-256, XAPP-3")
    assert [r.id for r in found] == ["APP-1", "OPS_2-9"]


def test_projects_are_remembered_from_the_tickets_kept_before() -> None:
    db = Database(":memory:")
    first = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    save_json_snapshot(db, "jira", JiraSnapshot(issues=[first]))
    db.migrate("jira", MIGRATIONS)  # the tickets move to their table, then their projects
    assert IssueRepository(db).projects() == ["APP"]
