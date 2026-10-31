"""`[slack]`."""

from pydantic import Field

from ...strict import Strict

DEFAULT_PING_FIRST = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
DEFAULT_PING_FOLLOW_UP = "Gentle bump on this review request"


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
