"""The notes' daily clean-up: todos done more than `prune_days` ago, and entries whose work
was all over that long ago (every PR merged or closed, every ticket resolved, no open todos).

When PRs closed and tickets were resolved comes from `graph.fetch`, asking github and jira,
since the work is usually long gone from their snapshots. Without jira, entries naming a
ticket are kept: nothing can say it's resolved.
"""

from datetime import UTC, date, datetime

from ...domain import Model
from ...graph import Entity, Graph
from .models import Entry
from .repository import NotesRepository

PR = "pr"
TICKET = "ticket"


def _by(day: date | None, cutoff: datetime) -> bool:
    """Whether `day` is on or before `cutoff`."""
    return day is not None and datetime.combine(day, datetime.min.time(), UTC) <= cutoff


class _Ended(Model):
    """What notes reads of a fetched `pr` (`closed_at`) or `ticket` (`resolved_at`)."""

    closed_at: str | None = None
    resolved_at: str | None = None


def _over_by(entity: Entity | None, cutoff: datetime) -> bool:
    """Whether the fetched entity says its work ended on or before `cutoff`."""
    if entity is None:
        return False
    ended = _Ended.model_validate(entity.attrs)
    when = ended.closed_at or ended.resolved_at
    return when is not None and datetime.fromisoformat(when) <= cutoff


def _finished(
    entry: Entry, cutoff: datetime, prs: dict[str, Entity], tickets: dict[str, Entity]
) -> bool:
    """Whether an entry's work was all over by `cutoff`, so the entry can go."""
    if entry.open_todos:
        return False
    urls = [link.ref for link in entry.links if link.kind == PR]
    keys = [link.ref for link in entry.links if link.kind == TICKET]
    if not urls and not keys:
        return _by(entry.updated_on, cutoff)
    return all(_over_by(prs.get(u), cutoff) for u in urls) and all(
        _over_by(tickets.get(k), cutoff) for k in keys
    )


def prune(notes: NotesRepository, graph: Graph, cutoff: datetime) -> list[str]:
    """Drop finished entries, and todos done by `cutoff`; returns what was done."""
    entries = notes.entries()
    candidates = [e for e in entries if not e.open_todos]
    links = [link for e in candidates for link in e.links]
    prs = graph.fetch(PR, sorted({link.ref for link in links if link.kind == PR}))
    tickets = graph.fetch(TICKET, sorted({link.ref for link in links if link.kind == TICKET}))
    notices: list[str] = []
    for entry in entries:
        if _finished(entry, cutoff, prs, tickets):
            notes.delete_entry(entry.id)
            notices.append(f"cleared entry {entry.title}")
            continue
        old = [t for t in entry.todos if _by(t.done_on, cutoff)]
        for todo in old:
            notes.delete_todo(todo.id)
        if old:
            notices.append(f"dropped {len(old)} old done todo(s) from {entry.title}")
    return notices
