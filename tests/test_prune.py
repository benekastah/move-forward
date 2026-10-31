from datetime import UTC, datetime, timedelta

from move_forward import prune
from move_forward.config import Config
from move_forward.db import Database
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.notes import markdown
from move_forward.plugins.notes.repository import MIGRATIONS, NotesRepository

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=100)
RECENT = NOW - timedelta(days=10)
PR = "https://github.com/acme/widgets/pull/"

WORK_NOTES = f"""\
# Work notes

## APP-1 long done
- Where: {PR}1
- Left to do:
- Done 2026-06-01: shipped
- Updated: 2026-06-01

## APP-2 done but still has a todo
- Where: {PR}2
- Left to do:
  - [quick] write the wrap-up
- Done 2026-06-01: shipped
- Done 2026-10-28: tested
- Updated: 2026-10-28

## APP-3 PR merged long ago, ticket open
- Where: {PR}3
- Left to do:
- Updated: 2026-06-01

## No ticket, no PR, stale
- Left to do:
- Updated: 2026-05-01

## {PR}4 PR without a ticket, merged recently
- Left to do:
- Updated: 2026-10-20
"""


CUTOFF = NOW - timedelta(days=90)


def _seeded() -> Database:
    """A database holding `WORK_NOTES`, as `mfd notes import` leaves it."""
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    entries, sections = markdown.read_work_notes(WORK_NOTES)
    NotesRepository(db).replace_all(entries, [], sections)
    return db


def pr_closed_at(url: str) -> datetime | None:
    return {PR + "1": OLD, PR + "2": OLD, PR + "3": OLD, PR + "4": RECENT}.get(url)


def issues_resolved_at(keys: list[str]) -> dict[str, datetime]:
    return {k: OLD for k in keys if k in ("APP-1", "APP-2")}


def test_prune_notes_clears_finished_entries_and_old_done_todos() -> None:
    db = _seeded()
    notices = prune.prune_notes(db, CUTOFF, pr_closed_at, issues_resolved_at)
    assert notices == [
        "cleared entry APP-1 long done",
        "dropped 1 old done todo(s) from APP-2 done but still has a todo",
        "cleared entry No ticket, no PR, stale",
    ]
    entries = NotesRepository(db).entries()
    assert [e.title for e in entries] == [
        "APP-2 done but still has a todo",
        "APP-3 PR merged long ago, ticket open",
        f"{PR}4 PR without a ticket, merged recently",
    ]
    assert [t.text for t in entries[0].todos] == ["write the wrap-up", "tested"]
    # A second run has nothing to do.
    assert prune.prune_notes(db, CUTOFF, pr_closed_at, issues_resolved_at) == []


def test_entries_with_tickets_are_kept_without_jira() -> None:
    notices = prune.prune_notes(_seeded(), CUTOFF, pr_closed_at, None)
    assert notices == [
        "dropped 1 old done todo(s) from APP-1 long done",  # kept: its ticket can't be checked
        "dropped 1 old done todo(s) from APP-2 done but still has a todo",
        "cleared entry No ticket, no PR, stale",
    ]


def test_plugins_prune_with_the_daily_run() -> None:
    config = Config(email="me@x", github=GitHubConfig(org="acme"))
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    runs: list[datetime] = []

    def plugins() -> list[str]:
        runs.append(NOW)
        return []

    prune.prune_if_due(config, db, NOW, plugins)
    prune.prune_if_due(config, db, NOW + timedelta(hours=1), plugins)
    assert len(runs) == 1
