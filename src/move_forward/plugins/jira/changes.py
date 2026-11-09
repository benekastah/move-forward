"""What changed between two Jira snapshots, as notification events (`Plugin.events`), and
which tickets can be closed now (`Plugin.graph_events`).

Each says something once, when a state changes; a ticket the earlier snapshot didn't hold
only ever says it was assigned.
"""

from ...api import Event, anchor
from ...api.kinds import TICKET
from .config import JiraConfig
from .models import JiraIssue, JiraSnapshot

KINDS = {
    "assigned": "a ticket is assigned to me",
    "priority_raised": "my ticket's priority goes up",
    "unblocked": "the tickets blocking mine are all done",
    "closeable": "my ticket's PRs are merged, so it can be closed",
}
"""The kinds, with what each says, for the settings page."""


def _event(issue: JiraIssue, kind: str, text: str) -> Event:
    return Event(
        kind=kind,
        thing=issue.key,
        title=f"{issue.key} {issue.summary}",
        text=text,
        anchor=anchor(TICKET, issue.url),
    )


def closeable(issue: JiraIssue) -> Event:
    """The event for a ticket whose PRs are merged."""
    return _event(issue, "closeable", "its PRs are merged: can be closed")


def _raised(config: JiraConfig, before: JiraIssue, after: JiraIssue) -> bool:
    """Whether the priority went up, by `config.priorities`' order; unlisted ones never do."""
    order = [p.casefold() for p in config.priorities]
    old, new = (p.casefold() if p else None for p in (before.priority, after.priority))
    return old in order and new in order and order.index(str(new)) > order.index(str(old))


def _changed(config: JiraConfig, old: JiraIssue, issue: JiraIssue) -> list[Event]:
    found: list[Event] = []
    if _raised(config, old, issue):
        found.append(_event(issue, "priority_raised", f"priority is now {issue.priority}"))
    if old.open_blockers and not issue.open_blockers:
        found.append(_event(issue, "unblocked", "no longer blocked"))
    return found


def changes(config: JiraConfig, before: JiraSnapshot, after: JiraSnapshot) -> list[Event]:
    """The events between two snapshots."""
    old = {i.key: i for i in before.issues}
    found: list[Event] = []
    for issue in after.issues:
        if (previous := old.get(issue.key)) is None:
            found.append(_event(issue, "assigned", "assigned to you"))
        else:
            found += _changed(config, previous, issue)
    return found
