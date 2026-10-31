"""The daily clean-up: work finished longer ago than `prune_days` leaves the notes and the DB.

From the notes: todos done that long ago, and whole entries whose work is over (every PR
merged or closed, every ticket resolved, no open todos) that long ago. From the database:
pings that old. It runs once a day from a refresh; see `prune_if_due`.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from .config import Config
from .db import CORE, Database
from .plugins.github import client as github
from .plugins.jira import client as jira
from .plugins.notes.models import Entry
from .plugins.notes.repository import NotesRepository
from .repositories import KvRepository

log = logging.getLogger(__name__)

LAST_PRUNE_KEY = "last_prune"
PRUNE_EVERY = timedelta(days=1)
PR = "pr"
TICKET = "ticket"

PrClosedAt = Callable[[str], datetime | None]
"""When a PR was merged or closed, by URL; None while it's open (or unknown)."""
IssuesResolvedAt = Callable[[list[str]], dict[str, datetime]]
"""When each of the given tickets was resolved; unresolved ones are left out."""


def _by(day: date | None, cutoff: datetime) -> bool:
    """Whether `day` is on or before `cutoff`."""
    return day is not None and datetime.combine(day, datetime.min.time(), UTC) <= cutoff


def _entry_finished(
    entry: Entry,
    cutoff: datetime,
    pr_closed_at: PrClosedAt,
    issues_resolved_at: IssuesResolvedAt | None,
) -> bool:
    """Whether an entry's work was all over by `cutoff`, so the entry can go."""
    if entry.open_todos:
        return False
    urls = [link.ref for link in entry.links if link.kind == PR]
    keys = [link.ref for link in entry.links if link.kind == TICKET]
    for url in urls:
        closed = pr_closed_at(url)
        if closed is None or closed > cutoff:
            return False
    if keys:
        if issues_resolved_at is None:
            return False  # can't check the tickets without Jira
        resolved = issues_resolved_at(keys)
        if any(key not in resolved or resolved[key] > cutoff for key in keys):
            return False
    return bool(urls or keys) or _by(entry.updated_on, cutoff)


def prune_notes(
    db: Database,
    cutoff: datetime,
    pr_closed_at: PrClosedAt,
    issues_resolved_at: IssuesResolvedAt | None,
) -> list[str]:
    """Drop finished entries, and todos done by `cutoff`; returns what was done."""
    notes = NotesRepository(db)
    notices: list[str] = []
    for entry in notes.entries():
        if _entry_finished(entry, cutoff, pr_closed_at, issues_resolved_at):
            notes.delete_entry(entry.id)
            notices.append(f"cleared entry {entry.title}")
            continue
        old = [t for t in entry.todos if _by(t.done_on, cutoff)]
        for todo in old:
            notes.delete_todo(todo.id)
        if old:
            notices.append(f"dropped {len(old)} old done todo(s) from {entry.title}")
    return notices


def _jira_lookup(config: Config) -> IssuesResolvedAt | None:
    if config.jira is None:
        return None
    jira_config = config.jira

    def lookup(keys: list[str]) -> dict[str, datetime]:
        return jira.resolved_at(
            jira_config, config.email, keys, jira.token(jira_config, config.email)
        )

    return lookup


def prune(
    config: Config,
    db: Database,
    now: datetime,
    pr_closed_at: PrClosedAt = github.pr_closed_at,
    issues_resolved_at: IssuesResolvedAt | None = None,
) -> list[str]:
    """Prune the notes and the database once; returns what was done."""
    cutoff = now - timedelta(days=config.prune_days)
    return prune_notes(db, cutoff, pr_closed_at, issues_resolved_at or _jira_lookup(config))


def _no_plugins() -> list[str]:
    return []


def prune_if_due(
    config: Config, db: Database, now: datetime, plugins: Callable[[], list[str]] = _no_plugins
) -> None:
    """Run `prune`, then `plugins` (the plugins' prunes), if they haven't run for a day. A
    failure is logged and retried the next day."""
    meta = KvRepository(db, CORE)
    last = meta.get(LAST_PRUNE_KEY)
    if last and datetime.fromisoformat(last) + PRUNE_EVERY > now:
        return
    meta.set(LAST_PRUNE_KEY, now.isoformat())
    try:
        for notice in prune(config, db, now):
            log.info("prune: %s", notice)
    except Exception:
        log.exception("prune failed")
    for notice in plugins():
        log.info("prune: %s", notice)
