"""The entity kinds jira publishes, with their attributes; see `docs/entities.md`."""

from ...api.kinds import Ticket


class JiraTicket(Ticket):
    """A `ticket` (the ticket family): one of my open Jira issues; `handle` is its key."""

    status: str
    status_category: str
    """Jira's `new`, `indeterminate` or `done`."""
    type: str
