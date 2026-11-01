import threading
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from move_forward import https
from move_forward.db import Database
from move_forward.domain import JiraIssue
from move_forward.plugin import Context
from move_forward.plugins.jira import client as jira
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
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


def test_the_token_is_never_sent_to_a_site_that_isnt_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_request(*_: object, **__: object) -> None:
        raise AssertionError("a request was made")

    monkeypatch.setattr(https.opener, "open", no_request)
    unvalidated = JiraConfig.model_construct(site="http://j.atlassian.net")
    with pytest.raises(RuntimeError, match="isn't https"):
        jira.search(unvalidated, "me@x", "jql", "token")


class _Redirect(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:9/steal")  # plain http, nothing listening
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def test_redirects_are_not_followed_with_the_token() -> None:
    httpd = HTTPServer(("127.0.0.1", 0), _Redirect)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    request = urllib.request.Request(
        f"http://127.0.0.1:{httpd.server_port}/rest",
        data=b"{}",
        headers={"Authorization": "Basic secret"},
        method="POST",
    )
    try:
        with pytest.raises(RuntimeError, match="not following it"):
            https.opener.open(request, timeout=5)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_resolved_tickets_are_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolved_at(
        config: JiraConfig, email: str, keys: list[str], api_token: str
    ) -> dict[str, datetime]:
        assert api_token == "t" and keys == ["APP-1", "APP-2"]
        return {"APP-1": datetime(2026, 6, 1, tzinfo=UTC)}

    def token(config: JiraConfig, email: str) -> str:
        return "t"

    monkeypatch.setattr(jira, "token", token)
    monkeypatch.setattr(jira, "resolved_at", resolved_at)
    db = Database(":memory:")
    ctx = Context(
        config=JiraConfig(site="https://j"),
        email="me@x",
        teams=[],
        prune_days=90,
        now=datetime(2026, 10, 30, tzinfo=UTC),
        db=db,
        kv=KvRepository(db, "jira"),
    )
    [fetched] = JiraPlugin().fetch(ctx, "ticket", ["APP-1", "APP-2"])
    assert (fetched.ref.id, fetched.url, fetched.attrs) == (
        "APP-1",
        "https://j/browse/APP-1",
        {"resolved_at": "2026-06-01T00:00:00+00:00"},
    )
    assert JiraPlugin().fetch(ctx, "pr", ["x"]) == []
