import urllib.error
import urllib.request
from datetime import UTC, date, datetime
from email.message import Message
from typing import cast

import pytest
from legacy import save_json_snapshot

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context, FetchRequest, LinkingRequest, SourceError
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


def test_other_configured_links_also_mean_blocked_by() -> None:
    raw = {
        "key": "X-1",
        "fields": {
            "created": "2026-10-01T00:00:00Z",
            "updated": "2026-10-01T00:00:00Z",
            "issuelinks": [
                {
                    "type": {"inward": "depends on", "outward": "is depended on by"},
                    "inwardIssue": {
                        "key": "X-2",
                        "fields": {"status": {"statusCategory": {"key": "new"}}},
                    },
                }
            ],
        },
    }
    parsed = jira.RawIssue.model_validate(raw)
    assert jira.to_issue(parsed, "https://j").open_blockers == []
    assert jira.to_issue(parsed, "https://j", None, ["depends on"]).open_blockers == ["X-2"]


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
    urls = ["https://j/browse/APP-1", "https://j/browse/OPS-2"]
    elsewhere = ["https://other/browse/APP-3", "https://j/browse/not a key", "APP-4"]
    fetched = JiraPlugin().fetch(FetchRequest(ctx, "ticket", [*urls, *elsewhere]))
    assert [(e.ref.id, e.url, e.attrs) for e in fetched] == [
        (resolved.url, resolved.url, {"resolved_at": "2026-06-01T00:00:00+00:00"}),
        (open_one.url, open_one.url, {"resolved_at": None}),
    ]
    assert IssueRepository(db).projects() == ["APP", "OPS"]
    assert JiraPlugin().fetch(FetchRequest(ctx, "pr", ["x"])) == []
    assert JiraPlugin().fetch(FetchRequest(ctx, "ticket", elsewhere)) == []  # without asking Jira


def test_tickets_move_from_the_json_snapshot_and_round_trip() -> None:
    db = Database(":memory:")
    labelled = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    bare = labelled.model_copy(update={"key": "APP-3", "labels": [], "open_blockers": []})
    save_json_snapshot(db, "jira", JiraSnapshot(issues=[labelled, bare]))
    db.migrate("jira", MIGRATIONS)
    repo = IssueRepository(db)
    assert repo.load() == JiraSnapshot(issues=[labelled, bare])
    due = bare.model_copy(update={"due": date(2026, 11, 5)})
    repo.replace(JiraSnapshot(issues=[due]))
    assert repo.load() == JiraSnapshot(issues=[due])
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
    assert plugin.linking_patterns(LinkingRequest(_ctx(db))) == {"ticket": None}  # nothing seen yet
    first = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    repo = IssueRepository(db)
    repo.replace(JiraSnapshot(issues=[first.model_copy(update={"key": "OPS_2-7"})]))
    repo.replace(JiraSnapshot(issues=[first]))  # OPS_2's last ticket is gone: still known
    assert repo.projects() == ["APP", "OPS_2"]
    graph = Graph(linking=plugin.linking_patterns(LinkingRequest(_ctx(db))))
    found = graph.named("APP-1, OPS_2-9, UTF-8, SHA-256, XAPP-3")
    assert [r.id for r in found] == ["APP-1", "OPS_2-9"]


def test_projects_are_remembered_from_the_tickets_kept_before() -> None:
    db = Database(":memory:")
    first = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    save_json_snapshot(db, "jira", JiraSnapshot(issues=[first]))
    db.migrate("jira", MIGRATIONS)  # the tickets move to their table, then their projects
    assert IssueRepository(db).projects() == ["APP"]


def test_a_key_jira_doesnt_know_leaves_out_only_that_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    known = jira.to_issue(jira.RawIssue.model_validate(RAW), "https://j")
    asked: list[str] = []

    def fake_search(_config: object, _email: str, jql: str, _token: str) -> list[JiraIssue]:
        asked.append(jql)
        if "APP-404" in jql:
            raise urllib.error.HTTPError("https://j", 400, "Bad Request", Message(), None)
        return [known] if "APP-1" in jql else []

    monkeypatch.setattr(jira, "search", fake_search)
    config = JiraConfig(site="https://j")
    assert jira.tickets(config, "me@x", ["APP-1", "APP-404"], "t") == [known]
    assert asked == ["key in (APP-1, APP-404)", "key in (APP-1)", "key in (APP-404)"]


def test_other_jira_errors_still_fail_the_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(_config: object, _email: str, jql: str, _token: str) -> list[JiraIssue]:
        raise urllib.error.HTTPError("https://j", 401, "Unauthorized", Message(), None)

    monkeypatch.setattr(jira, "search", fake_search)
    with pytest.raises(urllib.error.HTTPError):
        jira.tickets(JiraConfig(site="https://j"), "me@x", ["APP-1", "APP-2"], "t")


class _Closable:
    def close(self) -> None:
        pass


def _empty_search_with_myself(monkeypatch: pytest.MonkeyPatch, status: int) -> list[str]:
    """An empty search, and `/myself` answering with `status`; the paths asked."""
    asked: list[str] = []

    def fake_open(request: urllib.request.Request, timeout: float) -> _Closable:
        asked.append(request.full_url)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "", Message(), None)
        return _Closable()

    def fake_page(config: object, email: str, api_token: str, body: dict[str, object]) -> bytes:
        return b'{"issues": []}'

    monkeypatch.setattr(jira, "_search_page", fake_page)
    monkeypatch.setattr(jira.http.opener, "open", fake_open)
    return asked


def test_an_empty_search_fails_when_jira_rejects_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Jira answers a search with a rejected token as an anonymous user: 200 with no issues.
    asked = _empty_search_with_myself(monkeypatch, 401)
    with pytest.raises(SourceError, match="rejected the API token for me@x.*mfd token set jira"):
        jira.search(JiraConfig(site="https://j"), "me@x", "key in (APP-1)", "t")
    assert asked == ["https://j/rest/api/3/myself"]


def test_an_empty_search_is_empty_when_jira_accepts_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _empty_search_with_myself(monkeypatch, 200)
    assert jira.search(JiraConfig(site="https://j"), "me@x", "key in (APP-1)", "t") == []


CONFIG = JiraConfig(site="https://j")


def _jira_replies(monkeypatch: pytest.MonkeyPatch, offered: str) -> list[tuple[str, object]]:
    """Jira offering the transitions `offered`; the requests made are returned."""
    sent: list[tuple[str, object]] = []

    def request(
        config: JiraConfig,
        email: str,
        api_token: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> bytes:
        sent.append((path, body))
        return offered.encode()

    def token(config: JiraConfig, email: str) -> str:
        return "t"

    monkeypatch.setattr(jira, "token", token)
    monkeypatch.setattr(jira, "_request", request)
    return sent


def test_a_ticket_moves_by_the_transition_into_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _jira_replies(
        monkeypatch,
        '{"transitions": [{"id": "11", "to": {"name": "In Progress"}},'
        ' {"id": "21", "to": {"name": "In Review"}}]}',
    )
    jira.transition(CONFIG, "me@x.com", "APP-1", ["in review"])
    assert sent == [
        ("/rest/api/3/issue/APP-1/transitions", None),
        ("/rest/api/3/issue/APP-1/transitions", {"transition": {"id": "21"}}),
    ]


def test_a_ticket_moves_to_the_first_of_several_statuses_it_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _jira_replies(monkeypatch, '{"transitions": [{"id": "31", "to": {"name": "Done"}}]}')
    jira.transition(CONFIG, "me@x.com", "APP-1", ["Closed", "done"])
    assert sent[-1] == ("/rest/api/3/issue/APP-1/transitions", {"transition": {"id": "31"}})


def test_a_ticket_that_cant_go_there_says_where_it_can(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _jira_replies(monkeypatch, '{"transitions": [{"id": "11", "to": {"name": "Done"}}]}')
    with pytest.raises(LookupError, match="can't move to In Review; it can go to: Done"):
        jira.transition(CONFIG, "me@x.com", "APP-1", ["In Review"])
    assert len(sent) == 1  # nothing was changed


def _comment_json(id: str, account: str, body: object) -> dict[str, object]:
    return {
        "id": id,
        "author": {"accountId": account, "displayName": f"User {account}"},
        "created": "2026-10-20T10:00:00.000+0000",
        "body": body,
    }


def test_comments_tell_mine_from_others_and_find_mentions_of_me() -> None:
    mention = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "mention", "attrs": {"id": "me-id", "text": "@me"}}],
            }
        ],
    }
    other_mention = {"type": "mention", "attrs": {"id": "someone-else"}}
    fields = dict(cast(dict[str, object], RAW["fields"]))
    fields["comment"] = {
        "comments": [
            _comment_json("1", "me-id", "mine"),
            _comment_json("2", "bob", mention),
            _comment_json("3", "bob", other_mention),
        ]
    }
    raw = jira.RawIssue.model_validate({**RAW, "fields": fields})
    issue = jira.to_issue(raw, "https://jira", "me-id")
    assert [(c.id, c.mine, c.mentions_me) for c in issue.comments] == [
        ("1", True, False),
        ("2", False, True),
        ("3", False, False),
    ]
    assert jira.to_issue(raw, "https://jira").comments[0].mine is False  # account unknown


def test_only_the_latest_comments_are_kept() -> None:
    fields = dict(cast(dict[str, object], RAW["fields"]))
    fields["comment"] = {"comments": [_comment_json(str(n), "bob", "x") for n in range(30)]}
    raw = jira.RawIssue.model_validate({**RAW, "fields": fields})
    kept = jira.to_issue(raw, "https://jira").comments
    assert [c.id for c in kept] == [str(n) for n in range(20, 30)]
