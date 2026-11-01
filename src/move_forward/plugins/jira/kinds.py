"""The entity kinds jira publishes, with their attributes; see `docs/entities.md`."""

from ...domain import Model


class TicketAttrs(Model):
    """A `ticket`: one of my open Jira issues, by key."""

    summary: str
    status: str
    status_category: str
    """Jira's `new`, `indeterminate` or `done`."""
    type: str
