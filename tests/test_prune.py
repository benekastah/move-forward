from datetime import UTC, datetime, timedelta

from move_forward import prune
from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref
from move_forward.plugins.notes import markdown
from move_forward.plugins.notes import prune as notes_prune
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


CLOSED = {PR + "1": OLD, PR + "2": OLD, PR + "3": OLD, PR + "4": RECENT}
RESOLVED = {"APP-1": OLD, "APP-2": OLD}


def _graph(with_jira: bool = True) -> tuple[Graph, list[tuple[str, list[str]]]]:
    """A graph whose fetch answers as github and (unless not `with_jira`) jira would."""
    asked: list[tuple[str, list[str]]] = []

    def fetch(kind: str, ids: list[str]) -> list[Entity]:
        asked.append((kind, ids))
        if kind == "pr":
            return [
                Entity(
                    ref=Ref(kind="pr", id=u), title=u, attrs={"closed_at": CLOSED[u].isoformat()}
                )
                for u in ids
                if u in CLOSED
            ]
        if kind == "ticket" and with_jira:
            return [
                Entity(
                    ref=Ref(kind="ticket", id=k),
                    title=k,
                    attrs={"resolved_at": RESOLVED[k].isoformat()},
                )
                for k in ids
                if k in RESOLVED
            ]
        return []

    return Graph(fetch=fetch), asked


def test_finished_entries_and_old_done_todos_go() -> None:
    db = _seeded()
    graph, asked = _graph()
    notices = notes_prune.prune(NotesRepository(db), graph, CUTOFF)
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
    # One fetch per kind, for the entries with no open todos only.
    assert asked == [("pr", [PR + "1", PR + "3", PR + "4"]), ("ticket", ["APP-1", "APP-3"])]
    assert notes_prune.prune(NotesRepository(db), graph, CUTOFF) == []


def test_entries_with_tickets_are_kept_without_jira() -> None:
    graph, _ = _graph(with_jira=False)
    notices = notes_prune.prune(NotesRepository(_seeded()), graph, CUTOFF)
    assert notices == [
        "dropped 1 old done todo(s) from APP-1 long done",  # kept: its ticket can't be checked
        "dropped 1 old done todo(s) from APP-2 done but still has a todo",
        "cleared entry No ticket, no PR, stale",
    ]


def test_plugins_prune_once_a_day() -> None:
    db = Database(":memory:")
    runs: list[datetime] = []

    def plugins() -> list[str]:
        runs.append(NOW)
        return []

    prune.prune_if_due(db, NOW, plugins)
    prune.prune_if_due(db, NOW + timedelta(hours=1), plugins)
    assert len(runs) == 1
    prune.prune_if_due(db, NOW + timedelta(days=1), plugins)
    assert len(runs) == 2
