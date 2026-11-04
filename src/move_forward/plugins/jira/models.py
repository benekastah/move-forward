"""The jira plugin's data: my open tickets."""

from datetime import date, datetime

from pydantic import Field

from ...api import Model

MAX_COMMENTS = 10
"""Comments kept per ticket: the latest."""


class JiraComment(Model):
    """A comment on a ticket, as far as notifications need."""

    id: str
    author: str
    """Their display name."""
    created: datetime
    mine: bool = False
    """It's mine."""
    mentions_me: bool = False


class JiraIssue(Model):
    key: str
    summary: str
    status: str
    status_category: str
    """Jira's category key: new, indeterminate or done."""
    type: str
    labels: list[str] = Field(default_factory=list)
    priority: str | None = None
    created: datetime
    updated: datetime
    parent: str | None = None
    """The parent issue's key, e.g. the epic."""
    open_blockers: list[str] = Field(default_factory=list)
    """Keys of unfinished issues this one "is blocked by"."""
    resolved: datetime | None = None
    """Jira's resolution date, once the ticket is done."""
    due: date | None = None
    """Its due date, if it has one."""
    url: str
    comments: list[JiraComment] = Field(default_factory=list)
    """The latest `MAX_COMMENTS`, oldest first."""


class JiraSnapshot(Model):
    issues: list[JiraIssue]
