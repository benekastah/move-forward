"""Finding requested reviewers' Slack ids through Slack's API, by the emails github knows."""

import io
import json
import urllib.request
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import NOW, Reviewer, pr, snapshot, stored

from move_forward import https, keychain, render
from move_forward.https import RequestError
from move_forward.plugin import PruneRequest
from move_forward.plugins.slack import client, contacts
from move_forward.plugins.slack.models import Contact
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.refresh import build_dashboard, stored_graph
from move_forward.registry import enabled


def _lookups(monkeypatch: pytest.MonkeyPatch, ids: dict[str, str | None]) -> list[str]:
    asked: list[str] = []

    def lookup(api_token: str, email: str) -> str | None:
        assert api_token == "xoxb-test"
        asked.append(email)
        return ids.get(email)

    monkeypatch.setenv("SLACK_API_TOKEN", "xoxb-test")
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


@pytest.mark.parametrize(
    ("failure", "said"),
    [
        (
            client.SlackError("users.lookupByEmail", "missing_scope"),
            "said missing_scope: the token needs users:read.email",
        ),
        (
            client.SlackError("users.lookupByEmail", "token_expired"),
            "said token_expired: give slack a token that works: set $SLACK_API_TOKEN, or `mfd token set slack`",
        ),
        (RequestError("https://slack.com/api/users.lookupByEmail", "no network"), "no network"),
    ],
)
def test_a_lookup_that_fails_is_a_problem_until_a_refresh_has_none_fail(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, said: str
) -> None:
    failing = [True]

    def lookup(api_token: str, email: str) -> str | None:
        if failing[0]:
            raise failure
        return "U111111"

    monkeypatch.setenv("SLACK_API_TOKEN", "xoxb-test")
    monkeypatch.setattr(client, "lookup_by_email", lookup)
    config, db = stored(
        github=snapshot(mine=[pr(1, requested_users=["alice"])]),
        users={"alice": Reviewer(login="alice", email="a@x")},
    )
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [slack] = [e for e in plugins if e.name == "slack"]

    def problems() -> list[str]:
        return build_dashboard(config, db, NOW).plugin_problems.get("slack", [])

    assert contacts.look_up_requested(slack.ctx, graph) == []  # no refresh's traceback
    [shown] = problems()
    assert shown.startswith("looking up reviewers' Slack ids by email failed: ")
    assert said in shown
    assert ("token needs" in shown) == ("token needs" in said)  # only for a missing scope
    # Said on Slack's group, which its Nudge reviewers panel gives it, not in the footer.
    html = render.dashboard_page(build_dashboard(config, db, NOW), refreshing=frozenset())
    group, footer = html.split("data-key='slack'")[1].split("<footer>")
    assert render.e(shown) in group.split("<div class='panels'>")[0]
    assert render.e(shown) not in footer
    # The token given the scope, say, but nobody left to look up: the problem still goes.
    SlackRepository(db).save_looked_up("alice", None, NOW)
    assert contacts.look_up_requested(slack.ctx, graph) == []
    assert problems() == []


def test_without_a_token_nothing_is_looked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    def nothing_stored(service: str, account: str) -> None:
        return None

    def lookup(api_token: str, email: str) -> str | None:
        raise AssertionError("looked up without a token")

    monkeypatch.delenv("SLACK_API_TOKEN", raising=False)
    monkeypatch.setattr(keychain, "read", nothing_stored)
    monkeypatch.setattr(client, "lookup_by_email", lookup)
    config, db = stored(
        github=snapshot(mine=[pr(1, requested_users=["alice"])]),
        users={"alice": Reviewer(login="alice", email="a@x")},
    )
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [slack] = [e for e in plugins if e.name == "slack"]
    assert contacts.look_up_requested(slack.ctx, graph) == []
    assert "slack" not in build_dashboard(config, db, NOW).plugin_problems


def test_the_client_reads_slacks_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[dict[str, object]] = []
    sent: list[tuple[str, object, str | None]] = []

    def fake_open(request: urllib.request.Request, timeout: int) -> io.BytesIO:
        sent.append((request.full_url, request.data, request.get_header("Authorization")))
        return io.BytesIO(json.dumps(answers.pop(0)).encode())

    monkeypatch.setattr(https.opener, "open", fake_open)
    answers.append({"ok": True, "user": {"id": "U111111"}})
    assert client.lookup_by_email("t", "a+b@x") == "U111111"
    assert sent == [("https://slack.com/api/users.lookupByEmail", b"email=a%2Bb%40x", "Bearer t")]
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
    assert slack.plugin.prune(PruneRequest(slack.ctx, graph)) == []  # bob was looked up just now
    later = replace(slack.ctx, now=NOW + timedelta(days=config.prune_days + 1))
    assert slack.plugin.prune(PruneRequest(later, graph)) == ["will look up 1 person(s) again"]
    _lookups(monkeypatch, {"b@x": "U222222"})
    assert contacts.look_up_requested(later, graph) == ["bob"] and asked == ["b@x"]
