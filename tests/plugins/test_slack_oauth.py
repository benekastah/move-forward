"""Connecting to the Slack app: PKCE sign-in, keeping the token fresh, and slack's page."""

import base64
import hashlib
import json
from datetime import timedelta
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import ActionError, Context
from move_forward.plugins.slack import account, client, oauth, scopes
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.plugin import SlackPlugin
from move_forward.repositories import KvRepository

SERVICE = "move-forward-slack"


class FakeSecrets:
    """The system's secret store, as a dict by service."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.stored: dict[str, str] = {}
        monkeypatch.setattr(oauth.keychain, "read", self.read)
        monkeypatch.setattr(oauth.keychain, "write", self.write)
        monkeypatch.setattr(oauth.keychain, "delete", self.delete)
        monkeypatch.delenv("SLACK_API_TOKEN", raising=False)

    def read(self, service: str, account: str) -> str | None:
        return self.stored.get(service)

    def write(self, service: str, account: str, secret: str) -> None:
        self.stored[service] = secret

    def delete(self, service: str, account: str) -> None:
        self.stored.pop(service, None)


class FakeSlack:
    """Slack's Web API: answers in order, and what was asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.answers: list[dict[str, Any] | Exception] = []
        self.asked: list[tuple[str, str | None, dict[str, str]]] = []
        monkeypatch.setattr(oauth.client, "call", self.call)

    def call(self, method: str, api_token: str | None, params: dict[str, str]) -> dict[str, Any]:
        self.asked.append((method, api_token, params))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _ctx(db: Database, minutes: int = 0, client_id: str = "123.456") -> Context[SlackConfig]:
    return Context(
        config=SlackConfig(client_id=client_id),
        email="me@x",
        teams=[],
        prune_days=90,
        now=NOW + timedelta(minutes=minutes),
        db=db,
        kv=KvRepository(db, "slack"),
        dashboard_url="http://localhost:8765",
    )


SIGNED_IN = {
    "ok": True,
    "authed_user": {
        "id": "U1",
        "scope": "users:read,users:read.email",
        "access_token": "xoxe.xoxp-1",
        "token_type": "user",
        "refresh_token": "xoxe-1-r1",
        "expires_in": 43200,
    },
    "team": {"id": "T1", "name": "Acme"},
}


def _started(db: Database) -> dict[str, list[str]]:
    """Start signing in; the authorize URL's query."""
    return parse_qs(urlsplit(oauth.start(_ctx(db))).query, keep_blank_values=True)


def test_the_app_manifest_asks_for_exactly_the_features_scopes() -> None:
    manifest = json.loads(
        resources.files(oauth.__package__ or "").joinpath("manifest.json").read_text()
    )
    assert manifest["oauth_config"]["scopes"] == {"user": scopes.all_scopes()}  # no bot scopes
    assert manifest["oauth_config"]["pkce_enabled"] is True
    assert manifest["settings"]["token_rotation_enabled"] is True
    assert "http://localhost:8765/slack/callback" in manifest["oauth_config"]["redirect_urls"]


def test_connecting_signs_in_with_pkce_and_keeps_the_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets, slack, db = FakeSecrets(monkeypatch), FakeSlack(monkeypatch), Database(":memory:")
    query = _started(db)
    assert query["client_id"] == ["123.456"] and query["code_challenge_method"] == ["S256"]
    assert query["user_scope"] == [",".join(scopes.all_scopes())] and query["scope"] == [""]
    assert query["redirect_uri"] == ["http://localhost:8765/slack/callback"]
    slack.answers.append(SIGNED_IN)
    connected = oauth.finish(_ctx(db, 2), {"code": ["c0de"], "state": query["state"]})
    assert (connected.user_id, connected.team_name) == ("U1", "Acme")
    assert connected.expires_at == NOW + timedelta(minutes=2, hours=12)
    [(method, api_token, params)] = slack.asked
    assert (method, api_token) == ("oauth.v2.access", None)  # a public client: no secret
    verifier = params["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert query["code_challenge"] == [challenge.rstrip(b"=").decode()]
    assert params["redirect_uri"] == query["redirect_uri"][0] and "client_secret" not in params
    assert secrets.stored == {SERVICE: "xoxe.xoxp-1", f"{SERVICE}-refresh": "xoxe-1-r1"}
    with pytest.raises(oauth.SignInError, match="isn't the sign-in"):  # the state is used up
        oauth.finish(_ctx(db, 3), {"code": ["c0de"], "state": query["state"]})


@pytest.mark.parametrize(
    ("back", "why"),
    [
        ({"state": ["forged"], "code": ["c"]}, "isn't the sign-in"),
        ({"code": ["c"]}, "isn't the sign-in"),
        ({"error": ["access_denied"]}, "access_denied"),
    ],
)
def test_a_sign_in_that_isnt_ours_or_was_refused_is_turned_away(
    monkeypatch: pytest.MonkeyPatch, back: dict[str, list[str]], why: str
) -> None:
    secrets, slack, db = FakeSecrets(monkeypatch), FakeSlack(monkeypatch), Database(":memory:")
    state = _started(db)["state"]
    if why == "access_denied":
        back = {**back, "state": state}
    with pytest.raises(oauth.SignInError, match=why):
        oauth.finish(_ctx(db, 1), back)
    assert slack.asked == [] and secrets.stored == {}


def test_a_sign_in_that_took_too_long_is_turned_away(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSecrets(monkeypatch)
    slack, db = FakeSlack(monkeypatch), Database(":memory:")
    state = _started(db)["state"]
    with pytest.raises(oauth.SignInError, match="too long"):
        oauth.finish(_ctx(db, oauth.SIGN_IN_MINUTES + 1), {"code": ["c"], "state": state})
    assert slack.asked == []


def _connected(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeSecrets, FakeSlack, Database]:
    secrets, slack, db = FakeSecrets(monkeypatch), FakeSlack(monkeypatch), Database(":memory:")
    state = _started(db)["state"]
    slack.answers.append(SIGNED_IN)
    oauth.finish(_ctx(db), {"code": ["c"], "state": state})
    slack.asked.clear()
    return secrets, slack, db


def test_the_token_is_refreshed_before_it_runs_out_and_lost_when_slack_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets, slack, db = _connected(monkeypatch)
    assert oauth.token(_ctx(db, 60)) == "xoxe.xoxp-1" and slack.asked == []  # hours left
    slack.answers.append(
        {
            "ok": True,
            "access_token": "xoxe.xoxp-2",
            "refresh_token": "xoxe-1-r2",
            "expires_in": 43200,
        }
    )
    assert oauth.token(_ctx(db, 12 * 60 - 5)) == "xoxe.xoxp-2"  # five minutes left
    [(method, _, params)] = slack.asked
    assert method == "oauth.v2.access" and params["refresh_token"] == "xoxe-1-r1"
    assert secrets.stored[f"{SERVICE}-refresh"] == "xoxe-1-r2"  # the old one works no more
    slack.answers.append(client.SlackError("oauth.v2.access", "invalid_refresh_token"))
    assert oauth.token(_ctx(db, 24 * 60)) is None
    assert oauth.connection(_ctx(db)) is None and secrets.stored == {}
    assert "invalid_refresh_token" in (_ctx(db).kv.get(oauth.LOST) or "")


def test_without_the_app_the_token_set_by_hand_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets, db = FakeSecrets(monkeypatch), Database(":memory:")

    def by_hand(config: SlackConfig, email: str) -> str:
        return "xoxb-by-hand"

    monkeypatch.setattr(oauth.keychain, "token", by_hand)
    assert oauth.token(_ctx(db)) == "xoxb-by-hand" and secrets.stored == {}


def test_disconnecting_revokes_and_forgets_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets, slack, db = _connected(monkeypatch)
    slack.answers.append({"ok": True, "revoked": True})
    assert account.disconnect_action(_ctx(db), Graph([]), {}) == "disconnected Slack"
    assert slack.asked == [("auth.revoke", "xoxe.xoxp-1", {})]
    assert secrets.stored == {} and oauth.connection(_ctx(db)) is None
    with pytest.raises(ActionError, match="isn't connected"):
        account.disconnect_action(_ctx(db), Graph([]), {})


def test_slacks_page_sets_up_connects_and_shows_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(":memory:")
    setup = account.page(_ctx(db, client_id=""), Graph([]), {})
    assert "Set up the Slack app" in setup and "Connect Slack" not in setup
    assert "users:read.email" in setup  # the manifest, to paste
    assert account.connect(_ctx(db, client_id=""), Graph([]), {}) is None
    assert "href='/slack/connect'" in account.page(_ctx(db), Graph([]), {})
    connect = account.connect(_ctx(db), Graph([]), {}) or ""
    assert 'location.replace("https://slack.com/oauth/v2/authorize?' in connect
    _, _, db = _connected(monkeypatch)
    shown = account.page(_ctx(db), Graph([]), {})
    assert "Connected" in shown and "Acme" in shown and "slack/disconnect" in shown
    assert SlackPlugin.sign_in_pages == {oauth.CALLBACK_PATH}


def test_a_secret_store_that_wont_keep_the_token_is_said_not_crashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSecrets(monkeypatch)
    slack, db = FakeSlack(monkeypatch), Database(":memory:")

    def no_store(service: str, account: str, secret: str) -> None:
        raise FileNotFoundError("secret-tool")

    monkeypatch.setattr(oauth.keychain, "write", no_store)
    state = _started(db)["state"]
    slack.answers.append(SIGNED_IN)
    page = account.callback(_ctx(db), Graph([]), {"code": ["c"], "state": state})
    assert "Not connected: couldn&#x27;t keep the token" in page
    assert oauth.connection(_ctx(db)) is None
