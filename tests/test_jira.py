from datetime import UTC, datetime

import pytest

from move_forward.domain import JiraIssue
from move_forward.plugins.jira import client as jira
from move_forward.plugins.jira.config import JiraConfig

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


def test_resolved_at_builds_a_key_query(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    fields: dict[str, object] = dict(
        jira.RawIssue.model_validate(RAW).fields.model_dump(by_alias=True)
    )
    fields["created"] = fields["updated"] = "2026-10-01T00:00:00Z"

    def fake_search(_config: object, _email: str, jql: str, _token: str) -> list[JiraIssue]:
        seen.append(jql)
        return [
            jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j"),
            jira.to_issue(
                jira.RawIssue.model_validate(
                    {"key": "APP-2", "fields": {**fields, "resolutiondate": "2026-10-01T00:00:00Z"}}
                ),
                "https://j",
            ),
        ]

    monkeypatch.setattr(jira, "search", fake_search)
    config = JiraConfig(site="https://j")
    resolved = jira.resolved_at(config, "me@x", ["APP-1", "APP-2"], "t")
    assert seen == ["key in (APP-1, APP-2) AND statusCategory = Done"]
    assert list(resolved) == ["APP-2"] and resolved["APP-2"].year == 2026
    assert jira.resolved_at(config, "me@x", [], "t") == {} and len(seen) == 1
