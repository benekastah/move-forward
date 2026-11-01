"""Signing in to the Slack app, and keeping its user token fresh.

The app is a public client with PKCE: no client secret, so one app serves everyone, each
signing in for a user token of their own. Connect stores a single-use `state` and a PKCE
verifier and sends the browser to Slack; Slack sends it back to `/slack/callback` with a
code, which is exchanged for the tokens. Slack takes an `http://localhost` redirect from a
PKCE app, so the dashboard needs no certificate.

Tokens rotate: the access token lasts 12 hours and the refresh token 30 days, and each
refresh replaces both. They're kept in the system's secret store, the access token where
`mfd token set slack` puts a token and the refresh token beside it; what isn't secret (who
signed in, where, the scopes granted, when the access token runs out) is kept in kv.
"""

import base64
import contextlib
import hashlib
import hmac
import secrets
import threading
import urllib.error
from datetime import datetime, timedelta
from urllib.parse import urlencode

from pydantic import ValidationError

from ... import keychain
from ...domain import Model
from ...plugin import Context
from ...shell import CommandError
from . import client, scopes
from .config import SlackConfig

CALLBACK_PATH = "/slack/callback"
CONNECT_PATH = "/slack/connect"
PENDING = "oauth_pending"
"""The kv key of the sign-in started and not yet back."""
CONNECTION = "connection"
"""The kv key of the connection's details (`Connection`); empty when there's none."""
LOST = "connection_lost"
"""The kv key of why the last connection stopped working, to say on slack's page."""
SIGN_IN_MINUTES = 10
"""How long a started sign-in may take to come back."""
REFRESH_AHEAD = timedelta(minutes=10)
"""How long before the access token runs out it's refreshed."""

_refreshing = threading.Lock()
"""Held while the token is checked and refreshed: each refresh token works once, so two
refreshing at once would lose the connection."""


class Pending(Model):
    """A sign-in sent to Slack and not yet back."""

    state: str
    verifier: str
    redirect_uri: str
    started: datetime


class _Tokens(Model):
    """The tokens in an answer of Slack's oauth.v2.access, of the fields read here."""

    id: str = ""
    access_token: str = ""
    refresh_token: str | None = None
    expires_in: int | None = None
    """Seconds the access token lasts; None for one that doesn't run out."""
    scope: str = ""


class _Team(Model):
    id: str = ""
    name: str = ""


class _Answer(_Tokens):
    """oauth.v2.access's answer: a user token comes in `authed_user`. A refresh's may come at
    the top level instead; `user` takes whichever has one."""

    authed_user: _Tokens = _Tokens()
    team: _Team = _Team()

    @property
    def user(self) -> _Tokens:
        return self.authed_user if self.authed_user.access_token else self


class Connection(Model):
    """Who the stored token is for, and what it may do."""

    user_id: str
    team_id: str
    team_name: str = ""
    scopes: list[str]
    expires_at: datetime | None = None
    """When the access token runs out; None for one that doesn't (no rotation)."""


def refresh_service(config: SlackConfig) -> str:
    """Where the refresh token is kept in the secret store."""
    return f"{config.keychain_service}-refresh"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def start(ctx: Context[SlackConfig]) -> str:
    """Begin signing in: keep a fresh `state` and PKCE verifier, replacing any sign-in
    started before, and return the Slack URL to send the browser to."""
    pending = Pending(
        state=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(64),
        redirect_uri=ctx.dashboard_url + CALLBACK_PATH,
        started=ctx.now,
    )
    ctx.kv.set(PENDING, pending.model_dump_json())
    query = {
        "client_id": ctx.config.client_id,
        "scope": "",
        "user_scope": ",".join(scopes.all_scopes()),
        "redirect_uri": pending.redirect_uri,
        "state": pending.state,
        "code_challenge": _challenge(pending.verifier),
        "code_challenge_method": "S256",
    }
    return f"{client.AUTHORIZE}?{urlencode(query)}"


class SignInError(Exception):
    """Why a sign-in coming back was refused; shown on the page."""


def finish(ctx: Context[SlackConfig], query: dict[str, list[str]]) -> Connection:
    """Complete a sign-in Slack sent back: it must carry the `state` of the one started,
    within `SIGN_IN_MINUTES`; then its code is exchanged for tokens, which are stored.
    Raises SignInError when it isn't the one started, or Slack refused."""
    state = query.get("state", [""])[0]
    stored = ctx.kv.get(PENDING)
    try:
        pending = Pending.model_validate_json(stored) if stored else None
    except ValidationError:
        pending = None
    if pending is None or not state or not hmac.compare_digest(state, pending.state):
        raise SignInError("this isn't the sign-in this dashboard started; connect again")
    ctx.kv.set(PENDING, "")  # single use, whatever happens next
    if ctx.now - pending.started > timedelta(minutes=SIGN_IN_MINUTES):
        raise SignInError("the sign-in took too long; connect again")
    if error := query.get("error", [""])[0]:
        raise SignInError(f"Slack said {error}")
    code = query.get("code", [""])[0]
    if not code:
        raise SignInError("Slack sent no code back")
    try:
        answer = client.call(
            "oauth.v2.access",
            None,
            {
                "client_id": ctx.config.client_id,
                "code": code,
                "code_verifier": pending.verifier,
                "redirect_uri": pending.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    except (client.SlackError, urllib.error.URLError) as e:
        raise SignInError(str(e)) from e
    parsed = _Answer.model_validate(answer)
    user = parsed.authed_user
    if not user.access_token:
        raise SignInError("Slack sent no user token; does the app ask for user scopes?")
    connection = Connection(
        user_id=user.id,
        team_id=parsed.team.id,
        team_name=parsed.team.name,
        scopes=[s for s in user.scope.split(",") if s],
        expires_at=_expiry(ctx.now, user.expires_in),
    )
    try:
        _store(ctx, connection, user)
    except (ValueError, CommandError, OSError) as e:  # e.g. Linux without `secret-tool`
        raise SignInError(f"couldn't keep the token in the system's secret store: {e}") from e
    ctx.kv.set(LOST, "")
    return connection


def _expiry(now: datetime, expires_in: int | None) -> datetime | None:
    return now + timedelta(seconds=expires_in) if expires_in is not None else None


def _store(ctx: Context[SlackConfig], connection: Connection, tokens: _Tokens) -> None:
    keychain.write(ctx.config.keychain_service, ctx.email, tokens.access_token)
    if tokens.refresh_token:
        keychain.write(refresh_service(ctx.config), ctx.email, tokens.refresh_token)
    ctx.kv.set(CONNECTION, connection.model_dump_json())


def connection(ctx: Context[SlackConfig]) -> Connection | None:
    """The app's connection, if signed in."""
    stored = ctx.kv.get(CONNECTION)
    try:
        return Connection.model_validate_json(stored) if stored else None
    except ValidationError:
        return None


def token(ctx: Context[SlackConfig]) -> str | None:
    """The token to call Slack with: the app's, refreshed first when it's about to run out,
    else the one in `token_env` or stored by `mfd token set slack`; None without either.
    When Slack refuses the refresh (it ran out after 30 days, or was revoked), the
    connection is forgotten, and why is kept for slack's page."""
    with _refreshing:
        found = connection(ctx)
        if found is None:
            return keychain.token(ctx.config, ctx.email)
        access = keychain.read(ctx.config.keychain_service, ctx.email)
        if found.expires_at is None or ctx.now < found.expires_at - REFRESH_AHEAD:
            return access
        refresh = keychain.read(refresh_service(ctx.config), ctx.email)
        if refresh is None:
            _lose(ctx, "its token ran out and there's no refresh token")
            return None
        try:
            answer = client.call(
                "oauth.v2.access",
                None,
                {
                    "client_id": ctx.config.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                },
            )
        except client.SlackError as e:
            _lose(ctx, f"refreshing its token failed ({e.error})")
            return None
        except urllib.error.URLError:  # offline: the old token may still work
            return access if ctx.now < found.expires_at else None
        tokens = _Answer.model_validate(answer).user
        if not tokens.access_token:
            _lose(ctx, "Slack's refresh sent no token")
            return None
        renewed = found.model_copy(update={"expires_at": _expiry(ctx.now, tokens.expires_in)})
        try:
            _store(ctx, renewed, tokens)
        except (ValueError, CommandError, OSError) as e:
            # The old refresh token is spent: without the new one kept, the connection is
            # gone once this access token runs out. Use it meanwhile.
            _lose(ctx, f"couldn't keep its refreshed token: {e}")
        return tokens.access_token


def _lose(ctx: Context[SlackConfig], why: str) -> None:
    """Forget the connection, keeping why for slack's page. Its tokens are deleted if the
    secret store lets them be: they're of no use now either way."""
    ctx.kv.set(CONNECTION, "")
    ctx.kv.set(LOST, why)
    with contextlib.suppress(CommandError, OSError):
        forget(ctx)


def forget(ctx: Context[SlackConfig]) -> None:
    """Drop the connection and its tokens from the secret store."""
    ctx.kv.set(CONNECTION, "")
    keychain.delete(ctx.config.keychain_service, ctx.email)
    keychain.delete(refresh_service(ctx.config), ctx.email)


def disconnect(ctx: Context[SlackConfig]) -> None:
    """Sign out: revoke the token with Slack (if it still works), then forget it."""
    access = keychain.read(ctx.config.keychain_service, ctx.email)
    if access and connection(ctx) is not None:
        # Gone already, or offline: forgetting it is what matters here.
        with contextlib.suppress(client.SlackError, urllib.error.URLError):
            client.call("auth.revoke", access, {})
    forget(ctx)
