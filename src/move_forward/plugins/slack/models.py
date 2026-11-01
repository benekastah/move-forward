"""The slack plugin's data, as its tables hold it."""

from datetime import datetime
from typing import Literal

from ...domain import Model

PingKind = Literal["first", "follow-up"]


class Contact(Model):
    """Where a person's review pings go."""

    login: str
    """Their GitHub login, as PRs name requested reviewers."""
    slack_id: str | None = None
    """Their Slack member id, for a link to a DM."""


class TeamChannel(Model):
    """Where a team's review pings go."""

    slug: str
    """The team as PRs name it."""
    channel_name: str | None = None
    channel_id: str | None = None


class Ping(Model):
    """A review ping sent, so the next one to the same target is a follow-up."""

    pr_url: str
    target: str
    """`user:<login>`, `team:<name>`, or what the skill logged."""
    kind: PingKind
    message: str = ""
    sent_at: datetime
