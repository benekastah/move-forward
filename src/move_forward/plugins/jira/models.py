"""The jira plugin's data: my open tickets."""

from datetime import datetime

from pydantic import Field

from ...domain import Model


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
    url: str


class JiraSnapshot(Model):
    issues: list[JiraIssue]
