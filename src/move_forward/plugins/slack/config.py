"""`[slack]`."""

from pydantic import Field, field_validator

from ...strict import Strict

DEFAULT_PING_FIRST = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
DEFAULT_PING_FOLLOW_UP = "Gentle bump on this review request"
DEFAULT_SLACK_TOKEN_ENV = "SLACK_API_TOKEN"
DEFAULT_SLACK_KEYCHAIN_SERVICE = "move-forward-slack"


class SlackConfig(Strict):
    """`[slack]`: where review pings go."""

    workspace_url: str = ""
    """Base URL for Slack links, e.g. https://example.slack.com. Empty means no links."""
    ping_first: str = DEFAULT_PING_FIRST
    """The first review reminder; `{url}` is the PR."""
    ping_follow_up: str = DEFAULT_PING_FOLLOW_UP
    """The reminder once a reviewer has been pinged about the PR."""
    teams: dict[str, str] = Field(default_factory=dict)
    """Where review pings to a team go, e.g. `#team-a`, by its name in the top-level `teams`.
    Overrides the reviewer map."""
    token_env: str = DEFAULT_SLACK_TOKEN_ENV
    """The environment variable holding a Slack token with the `users:read.email` scope, for
    finding requested reviewers' Slack ids by their email. No token, no lookups."""
    keychain_service: str = DEFAULT_SLACK_KEYCHAIN_SERVICE
    """The macOS keychain item's service holding that token, when `token_env` isn't set; its
    account is the top-level `email`."""
    team_channel: str = ""
    """The channel of a team nothing else names one for, from its name: e.g. `#team-{team}`.
    Empty means no guess."""

    @field_validator("team_channel")
    @classmethod
    def _pattern(cls, value: str) -> str:
        if value and (not value.startswith("#") or "{team}" not in value):
            raise ValueError(
                f"{value!r} must be a channel name with {{team}} in it, e.g. #team-{{team}}"
            )
        return value

    def guessed_channel(self, team: str) -> str | None:
        """The channel `team_channel` gives `team`, if it's set."""
        return self.team_channel.replace("{team}", team) if self.team_channel else None
