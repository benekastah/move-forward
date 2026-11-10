"""`[slack]`."""

from string import Formatter
from typing import ClassVar, cast

from pydantic import Field, field_validator, model_validator

from ...api.keychain import TokenFields

DEFAULT_PING_FIRST = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
DEFAULT_PING_FOLLOW_UP = "Gentle bump on this review request"
DEFAULT_SLACK_TOKEN_ENV = "SLACK_API_TOKEN"
DEFAULT_SLACK_KEYCHAIN_SERVICE = "move-forward-slack"


class SlackConfig(TokenFields):
    """`[slack]`: where review pings go.

    Its token (`token_env`, else the system's secret store) is one with the
    `users:read.email` scope, for finding requested reviewers' Slack ids by their email. No
    token, no lookups. Explicit channel discovery also needs channels:read and groups:read.
    """

    default_token_env: ClassVar[str] = DEFAULT_SLACK_TOKEN_ENV
    default_keychain_service: ClassVar[str] = DEFAULT_SLACK_KEYCHAIN_SERVICE

    token_env: str = ""
    """The environment variable holding a Slack token with the `users:read.email` scope, for
    finding requested reviewers' Slack ids by their email; no token, no lookups.
    `SLACK_API_TOKEN` unless set. Channel discovery also needs channels:read and groups:read."""
    keychain_service: str = ""
    """The service that token is stored under, by `mfd token set slack`, in the system's
    secret store (the macOS keychain, Windows' Credential Manager, the desktop keyring), when
    `token_env` isn't set; its account is the top-level `email`. `move-forward-slack` unless
    set."""
    workspace_url: str = ""
    """Base URL for Slack links, e.g. https://example.slack.com. Empty means no links."""
    ping_first: str = DEFAULT_PING_FIRST
    """The first review reminder; `{url}` is the PR, and a brace of its own is written `{{` or
    `}}`."""
    ping_follow_up: str = DEFAULT_PING_FOLLOW_UP
    """The reminder once a reviewer has been pinged about the PR, as written: nothing in it is
    filled in, and a brace is a brace."""
    teams: dict[str, str] = Field(default_factory=dict)
    """Where review pings to a team go, e.g. `#team-a`, by its name in the top-level `teams`.
    Wins over a channel set by hand (`mfd slack team set`)."""

    @model_validator(mode="before")
    @classmethod
    def _gone(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        settings = cast(dict[str, object], data)
        if "client_id" in settings:
            raise ValueError(
                "`client_id` is gone, with the Slack app's sign-in: delete the line; for"
                " looking up reviewers' Slack ids, give a token with users:read.email"
                " (`mfd token set slack`, or $SLACK_API_TOKEN)"
            )
        if "team_channel" in settings:
            raise ValueError(
                "`team_channel` is gone, with its guess at a team's channel: delete the line,"
                ' and name each team\'s channel in [slack.teams] (e.g. web = "#team-web"), or'
                " set it with `mfd slack team set <team> <#channel>`"
            )
        return settings

    @field_validator("workspace_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ValueError(f"{value!r} must be an https:// URL, e.g. https://example.slack.com")
        return value

    @field_validator("ping_first")
    @classmethod
    def _ping_first(cls, value: str) -> str:
        try:
            fields = [
                (f, spec, conv) for _, f, spec, conv in Formatter().parse(value) if f is not None
            ]
        except ValueError:  # a lone brace
            fields = [("", "", None)]
        if any(field != ("url", "", None) for field in fields):  # a bare {url}, nothing else
            raise ValueError(
                f"{value!r} can name only {{url}}, and a brace of its own is written {{{{ or }}}}"
            )
        return value
