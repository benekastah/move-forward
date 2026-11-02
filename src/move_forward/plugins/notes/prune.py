"""The notes' daily clean-up: todos done more than `prune_days` ago, entries whose work was
all over that long ago (every PR merged or closed, every ticket resolved, no open todos),
PRs, tickets and entries on ice whose work is over the same way, and offers last made that
long ago.

When PRs closed and tickets were resolved comes from `graph.fetch`, asking github and jira,
since the work is usually long gone from their snapshots. Without jira, ticket keys aren't
known as such, so an entry naming only tickets goes once it hasn't been updated that long.
"""

from datetime import UTC, date, datetime

from ...domain import Model
from ...graph import Entity, Graph
from .models import Entry, Ice
from .panels import entry_keys
from .repository import NotesRepository

PR = "pr"
TICKET = "ticket"
ENTRY = "entry"
BRANCH = "branch"


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
    entry: Entry,
    keys: list[str],
    cutoff: datetime,
    prs: dict[str, Entity],
    tickets: dict[str, Entity],
) -> bool:
    """Whether an entry's work (its PRs, and `keys`, its tickets) was all over by `cutoff`, so
    the entry can go."""
    if entry.open_todos:
        return False
    urls = [link.ref for link in entry.links if link.kind == PR]
    if not urls and not keys:
        return _by(entry.updated_on, cutoff)
    return all(_over_by(prs.get(u), cutoff) for u in urls) and all(
        _over_by(tickets.get(k), cutoff) for k in keys
    )


def _ice_over(
    ice: Ice,
    entries: set[str],
    cutoff: datetime,
    prs: dict[str, Entity],
    tickets: dict[str, Entity],
) -> bool:
    """Whether what an act of icing put on ice on its own is all over by `cutoff`: its PRs
    closed, its tickets resolved, its entries cleared. Stacks stay until they're reopened:
    they keep the commits to restore their branches from."""
    if not ice.items or any(item.kind == BRANCH for item in ice.items):
        return False
    return all(
        item.ref_id not in entries
        if item.kind == ENTRY
        else _over_by((prs if item.kind == PR else tickets).get(item.ref_id), cutoff)
        for item in ice.items
    )


def prune(notes: NotesRepository, graph: Graph, cutoff: datetime) -> list[str]:
    """Drop finished entries, todos done by `cutoff`, and what's on ice whose work is over;
    returns what was done."""
    entries = notes.entries()
    ices = notes.ices()
    keys = {e.id: entry_keys(e, graph) for e in entries}
    candidates = [e for e in entries if not e.open_todos]
    iced = [item for ice in ices for item in ice.items]
    urls = {link.ref for e in candidates for link in e.links if link.kind == PR}
    prs = graph.fetch(PR, sorted(urls | {i.ref_id for i in iced if i.kind == PR}))
    ticket_keys = {k for e in candidates for k in keys[e.id]}
    tickets = graph.fetch(
        TICKET, sorted(ticket_keys | {i.ref_id for i in iced if i.kind == TICKET})
    )
    notices: list[str] = []
    for entry in entries:
        if _finished(entry, keys[entry.id], cutoff, prs, tickets):
            notes.delete_entry(entry.id)
            notices.append(f"cleared entry {entry.title}")
            continue
        old = [t for t in entry.todos if _by(t.done_on, cutoff)]
        for todo in old:
            notes.delete_todo(todo.id)
        if old:
            notices.append(f"dropped {len(old)} old done todo(s) from {entry.title}")
    left = {e.title for e in notes.entries()}
    for ice in ices:
        if _ice_over(ice, left, cutoff, prs, tickets):
            notes.delete_ice(ice.id)
            notices.append(f"took {ice.title} off ice: its work is over")
    if forgotten := notes.forget_offers_before(cutoff):
        notices.append(f"forgot {forgotten} old offer(s)")
    return notices
