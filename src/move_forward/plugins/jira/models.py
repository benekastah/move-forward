"""The jira plugin's data: my open tickets."""

from datetime import date, datetime

from pydantic import Field

from ...api import Model


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
    """When it was done: the later of its resolution date and when it moved into a done
    status; None while its status isn't done."""
    due: date | None = None
    """Its due date, if it has one."""
    url: str


class JiraSnapshot(Model):
    issues: list[JiraIssue]
