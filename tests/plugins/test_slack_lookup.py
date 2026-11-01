"""Finding requested reviewers' Slack ids through Slack's API, by the emails github knows."""

import io
import json
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import NOW, Reviewer, pr, snapshot, stored

from move_forward import https
from move_forward.plugins.slack import client, contacts
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.models import Contact
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.refresh import stored_graph
from move_forward.registry import enabled


def _token(value: str | None) -> Callable[[SlackConfig, str], str | None]:
    """Stands in for `client.token`: the token found, whatever the config and email."""

    def token(config: SlackConfig, email: str) -> str | None:
        return value

    return token


def _lookups(monkeypatch: pytest.MonkeyPatch, ids: dict[str, str | None]) -> list[str]:
    asked: list[str] = []

    def lookup(api_token: str, email: str) -> str | None:
        assert api_token == "xoxb-test"
        asked.append(email)
        return ids.get(email)

    monkeypatch.setattr(client, "token", _token("xoxb-test"))
    monkeypatch.setattr(client, "lookup_by_email", lookup)
    return asked


def test_requested_reviewers_are_looked_up_once_each(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _lookups(monkeypatch, {"a@x": "U111111"})
    config, db = stored(
        github=snapshot(mine=[pr(1, requested_users=["alice", "bob", "carol", "dan"])]),
        users={
            "alice": Reviewer(login="alice", email="a@x"),
            "bob": Reviewer(login="bob", email="b@x"),
            "carol": Reviewer(login="carol", email="c@x", slack_id="U333333"),
        },
    )
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [slack] = [e for e in plugins if e.name == "slack"]
    assert contacts.look_up_requested(slack.ctx, graph) == ["alice"]
    # carol's id was imported; dan has no email; bob has no Slack account, and that's kept.
    assert sorted(asked) == ["a@x", "b@x"]
    repo = SlackRepository(db)
    assert {k: c.slack_id for k, c in repo.contacts().items()} == {
        "alice": "U111111",
        "bob": None,
        "carol": "U333333",
    }
    assert repo.sources()["user:alice"] == "lookup"
    assert contacts.look_up_requested(slack.ctx, graph) == [] and len(asked) == 2
    # An import replaces a lookup; setting by hand beats both.
    repo.replace_imported([Contact(login="alice", slack_id="U999999")], [], [])
    assert repo.contacts()["alice"].slack_id == "U999999"
    # Someone the import names without a Slack id is still looked up, and a lookup stays.
    repo.replace_imported([Contact(login="bob"), Contact(login="erin")], [], [])
    assert "erin" not in repo.contacts() and repo.sources()["user:bob"] == "lookup"
    repo.set_contact("alice", "U000000")
    repo.replace_imported([Contact(login="alice", slack_id="U999999")], [], [])
    assert repo.contacts()["alice"].slack_id == "U000000"


def test_without_a_token_nothing_is_looked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "token", _token(None))
    config, db = stored(github=snapshot(mine=[pr(1, requested_users=["alice"])]), slack=True)
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [slack] = [e for e in plugins if e.name == "slack"]
    assert contacts.look_up_requested(slack.ctx, graph) == []


def test_the_client_reads_slacks_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[dict[str, object]] = []
    urls: list[str] = []

    def fake_open(request: urllib.request.Request, timeout: int) -> io.BytesIO:
        urls.append(request.full_url)
        return io.BytesIO(json.dumps(answers.pop(0)).encode())

    monkeypatch.setattr(https.opener, "open", fake_open)
    answers.append({"ok": True, "user": {"id": "U111111"}})
    assert client.lookup_by_email("t", "a+b@x") == "U111111"
    assert urls == ["https://slack.com/api/users.lookupByEmail?email=a%2Bb%40x"]
    answers.append({"ok": False, "error": "users_not_found"})
    assert client.lookup_by_email("t", "a@x") is None
    answers.append({"ok": False, "error": "missing_scope"})
    with pytest.raises(RuntimeError, match="missing_scope"):
        client.lookup_by_email("t", "a@x")


def test_a_lookup_that_found_nobody_is_tried_again_after_prune_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _lookups(monkeypatch, {})
    config, db = stored(
        github=snapshot(mine=[pr(1, requested_users=["bob"])]),
        users={"bob": Reviewer(login="bob", email="b@x")},
    )
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [slack] = [e for e in plugins if e.name == "slack"]
    contacts.look_up_requested(slack.ctx, graph)
    assert slack.plugin.prune(slack.ctx, graph) == []  # bob was looked up just now
    later = replace(slack.ctx, now=NOW + timedelta(days=config.prune_days + 1))
    assert slack.plugin.prune(later, graph) == ["will look up 1 person(s) again"]
    _lookups(monkeypatch, {"b@x": "U222222"})
    assert contacts.look_up_requested(later, graph) == ["bob"] and asked == ["b@x"]
