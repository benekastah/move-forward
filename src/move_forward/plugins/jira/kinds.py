"""The entity kinds jira publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...api.kinds import Ticket


class JiraTicket(Ticket):
    """A `ticket` (the ticket family): one of my open Jira issues; `handle` is its key."""

    status: str
    status_category: str
    """Jira's `new`, `indeterminate` or `done`."""
    type: str
    open_blockers: list[str] = Field(default_factory=list[str])
    """The keys of the unfinished tickets blocking it."""
