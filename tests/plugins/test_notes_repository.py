"""The notes plugin's tables, through its repository."""

import threading
from datetime import date, timedelta, timezone

import pytest

from move_forward import localtime
from move_forward.db import Database
from move_forward.plugins.notes.models import (
    Condition,
    IcedItem,
    Link,
)
from move_forward.plugins.notes.repository import MIGRATIONS, NotesRepository

DAY = date(2026, 10, 30)
PR = Link(kind="pull_request", ref="https://github.com/acme/widgets/pull/1")
URL = "https://acme.atlassian.net/browse/APP-1"


@pytest.fixture
def notes() -> NotesRepository:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    return NotesRepository(db)


def test_an_entry_with_todos_in_order(notes: NotesRepository) -> None:
    entry = notes.add_entry("APP-1 Fix it", DAY, status="in review", links=[PR])
    first = notes.add_todo(entry, "reply", "quick", links=[PR])
    second = notes.add_todo(entry, "merge")
    notes.set_done(first, DAY)
    [read] = notes.entries()
    assert (read.title, read.status, read.updated_on, read.links) == (
        "APP-1 Fix it",
        "in review",
        DAY,
        [PR],
    )
    assert [(t.id, t.text, t.size, t.done_on) for t in read.todos] == [
        (first, "reply", "quick", DAY),
        (second, "merge", None, None),
    ]
    assert read.todos[0].links == [PR] and [t.id for t in read.open_todos] == [second]
    assert notes.entry_id("APP-1 Fix it") == entry and notes.entry_id("nope") is None


def test_todos_reopen_get_links_and_go(notes: NotesRepository) -> None:
    entry = notes.add_entry("e", DAY)
    todo = notes.add_todo(entry, "t")
    notes.set_done(todo, DAY)
    notes.set_done(todo, None)
    notes.link_todo(todo, PR)
    notes.link_todo(todo, PR)  # twice is once
    [read] = notes.entries()[0].todos
    assert read.done_on is None and read.links == [PR]
    notes.delete_todo(todo)
    assert notes.entries()[0].todos == []


def test_icing_and_thawing(notes: NotesRepository) -> None:
    items = [
        IcedItem(
            kind="git.branch",
            ref_id="/src/w\tme/bottom",
            name="me/bottom",
            repo_dir="/src/w",
            sha="a1",
        ),
        IcedItem(
            kind="git.branch", ref_id="/src/w\tme/top", name="me/top", repo_dir="/src/w", sha="b2"
        ),
    ]
    ice = notes.add_ice("me/bottom", DAY, "parked", items)
    [read] = notes.ices()
    assert (read.id, read.title, read.iced_on, read.note, read.items) == (
        ice,
        "me/bottom",
        DAY,
        "parked",
        items,
    )
    notes.delete_ice(ice)
    assert notes.ices() == [] and notes.is_empty()


def test_branches_iced_before_names_were_kept_get_theirs_from_their_ids() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:2])
    db.execute("INSERT INTO notes_ice (id, title, iced_on) VALUES (1, 'me/b', '2026-10-01')")
    db.execute("INSERT INTO notes_iced VALUES (1, 0, 'branch', ?, 'a1')", ("/src/w\tme/b",))
    db.migrate("notes", MIGRATIONS)
    [ice] = NotesRepository(db).ices()
    assert ice.items == [
        IcedItem(kind="git.branch", ref_id="/src/w\tme/b", name="me/b", repo_dir="/src/w", sha="a1")
    ]


def test_kinds_kept_before_they_were_named_for_their_plugins_are_renamed() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:6])
    pr = "https://github.com/acme/w/pull/1"
    db.execute("INSERT INTO notes_entries (id, title) VALUES (1, 'APP-1 Fix')")
    db.execute("INSERT INTO notes_links VALUES (1, 'pr', ?), (1, 'ticket', 'APP-1')", (pr,))
    db.execute("INSERT INTO notes_todos (id, entry_id, position, text) VALUES (1, 1, 0, 'x')")
    db.execute("INSERT INTO notes_todo_links VALUES (1, 'pr', ?)", (pr,))
    for ice_id, kind, ref_id in [
        (1, "branch", "/w\tme/b"),
        (2, "pr", pr),
        (3, "entry", "APP-1 Fix"),
    ]:
        db.execute(
            "INSERT INTO notes_ice (id, title, iced_on) VALUES (?, 't', '2026-10-01')", (ice_id,)
        )
        db.execute(
            "INSERT INTO notes_iced (ice_id, position, kind, ref_id) VALUES (?, 0, ?, ?)",
            (ice_id, kind, ref_id),
        )
    db.execute("INSERT INTO notes_ice_covers VALUES (1, 'pr', ?)", (pr,))
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    [entry] = notes.entries()
    assert entry.links == [Link(kind="pull_request", ref=pr), Link(kind="ticket", ref="APP-1")]
    assert entry.todos[0].links == [Link(kind="pull_request", ref=pr)]
    kinds = [item.kind for ice in notes.ices() for item in ice.items]
    assert kinds == ["git.branch", "pull_request", "notes.entry"]
    assert [r["kind"] for r in db.query("SELECT kind FROM notes_ice_covers")] == ["pull_request"]


def test_check_ins_written_as_declines_are_remembered_by_their_keys() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:2])  # before check-ins were fields
    db.execute("INSERT INTO notes_entries (id, title) VALUES (1, 'APP-1 Fix it')")
    for position, (on, text) in enumerate(
        [
            ("2026-10-01", "Checked"),
            ("2026-10-05", "Checked"),
            ("2026-10-03", "Asked-done"),
            ("2026-10-04", "not today"),
        ]
    ):
        db.execute("INSERT INTO notes_declined VALUES (1, ?, ?, ?)", (position, on, text))
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    assert notes.recall("checked:APP-1 Fix it") == date(2026, 10, 5)
    assert notes.recall("asked-done:APP-1 Fix it") == date(2026, 10, 3)
    assert notes.recall("decline:APP-1 Fix it:not today") == date(2026, 10, 4)


def test_the_bookkeeping_commands_rows_are_remembered_under_their_keys() -> None:
    """What `decline`, `checked`, `asked-done`, `offered` and `asked` kept is copied into
    `notes_remembered` under the keys their CHANGELOG mapping gives, and their tables go."""
    db = Database(":memory:")
    names = [m.name for m in MIGRATIONS]
    db.migrate("notes", MIGRATIONS[: names.index("0009_remembered")])
    db.execute(
        "INSERT INTO notes_entries (id, title, checked_on, asked_done_on)"
        " VALUES (1, 'APP-1 Fix it', '2026-10-05', '2026-10-03'), (2, 'Chores', NULL, NULL)"
    )
    for position, (on, text) in enumerate(
        [("2026-10-01", "nudge"), ("2026-10-04", "nudge"), ("2026-10-02", "fresh review")]
    ):
        db.execute("INSERT INTO notes_declined VALUES (1, ?, ?, ?)", (position, on, text))
    pr = "https://github.com/acme/w/pull/1"
    db.execute(
        "INSERT INTO notes_offers VALUES (?, 'nudge', '2026-10-06T23:30:00+00:00'),"
        " (?, 'fresh-review', '2026-10-07T08:00:00+00:00')",
        (pr, pr),
    )
    db.execute(
        "INSERT INTO kv VALUES ('notes', 'last_asked_ice', '2026-10-08'),"
        " ('notes', 'last_asked_standup', '2026-10-09'), ('notes', 'other', 'kept'),"
        " ('core', 'last_asked_ice', 'not notes')"
    )
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    found = {r["key"]: r["on_date"] for r in db.query("SELECT * FROM notes_remembered")}
    assert found == {
        "decline:APP-1 Fix it:nudge": "2026-10-04",  # the latest
        "decline:APP-1 Fix it:fresh review": "2026-10-02",
        "checked:APP-1 Fix it": "2026-10-05",
        "asked-done:APP-1 Fix it": "2026-10-03",
        f"offered:{pr}:nudge": "2026-10-06",  # the local day (UTC, in tests)
        f"offered:{pr}:fresh-review": "2026-10-07",
        "asked:ice": "2026-10-08",
        "asked:standup": "2026-10-09",
    }
    assert notes.recall("asked:ice") == date(2026, 10, 8)
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not tables & {"notes_declined", "notes_offers"}
    kv = {(r["plugin"], r["key"]) for r in db.query("SELECT plugin, key FROM kv")}
    assert kv == {  # core's table is only read
        ("notes", "last_asked_ice"),
        ("notes", "last_asked_standup"),
        ("notes", "other"),
        ("core", "last_asked_ice"),
    }


def test_an_offers_time_is_remembered_as_its_local_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(localtime, "zone", timezone(timedelta(hours=-7)))  # Pacific, in summer
    db = Database(":memory:")
    names = [m.name for m in MIGRATIONS]
    db.migrate("notes", MIGRATIONS[: names.index("0009_remembered")])
    pr = "https://github.com/acme/w/pull/1"
    db.execute("INSERT INTO notes_offers VALUES (?, 'nudge', '2026-10-07T03:00:00+00:00')", (pr,))
    db.migrate("notes", MIGRATIONS)
    assert NotesRepository(db).recall(f"offered:{pr}:nudge") == date(2026, 10, 6)


def test_remember_replaces_the_day_and_recall_gives_it_by_key() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    assert notes.recall("asked:ice") is None
    notes.remember("asked:ice", date(2026, 10, 30))
    notes.remember("asked:ice", date(2026, 10, 1))  # what I said last, even if earlier
    notes.remember("asked:standup", DAY)
    assert notes.recall("asked:ice") == date(2026, 10, 1)


def test_conditions_kept_before_they_named_their_pr_are_given_one_once() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:5])  # before conditions named their PR
    one, two = "https://github.com/acme/w/pull/1", "https://github.com/acme/w/pull/2"
    db.execute("INSERT INTO notes_entries (id, title) VALUES (1, 'APP-1 two PRs')")
    db.execute("INSERT INTO notes_entries (id, title) VALUES (2, 'APP-2 no PR')")
    for ref in (one, two):
        db.execute("INSERT INTO notes_links VALUES (1, 'pr', ?)", (ref,))
    db.execute("INSERT INTO notes_conditions VALUES (1, 0, ?)", (f"wait for {two} to deploy",))
    db.execute("INSERT INTO notes_conditions VALUES (1, 1, 'after the release')")
    db.execute("INSERT INTO notes_conditions VALUES (2, 0, 'ask Sam')")
    db.migrate("notes", MIGRATIONS)
    first, second = NotesRepository(db).entries()
    assert first.conditions == [
        Condition(pr=two, text=f"wait for {two} to deploy"),
        Condition(pr=one, text="after the release"),
        Condition(pr=two, text="after the release"),
    ]
    assert second.conditions == [] and second.lines == ["- Merge condition: ask Sam"]
    assert db.migrate("notes", MIGRATIONS) == 0  # once


def test_rekeying_asks_for_new_ids_outside_its_transaction() -> None:
    """What gives the new ids asks plugins (their `address`), which may use the database."""
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    notes.add_entry("e", DAY, links=[Link(kind="jira.issue", ref="APP-1")])

    def identify(kind: str, ref: str) -> str:
        # A plugin's address waiting on another thread that reads the database, say.
        reader = threading.Thread(target=db.query, args=("SELECT 1",))
        reader.start()
        reader.join(timeout=2)
        assert not reader.is_alive(), "identify ran holding the database"
        return URL

    assert notes.rekey(["jira.issue"], identify) == 1
    assert [link.ref for link in notes.entries()[0].links] == [URL]


def test_rekeying_moves_links_todos_links_and_whats_on_ice_once_each() -> None:
    """Where the new id is there already, the old row goes: a thing is linked, or on ice,
    once."""
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS)
    notes = NotesRepository(db)
    old, new = Link(kind="jira.issue", ref="APP-1"), Link(kind="jira.issue", ref=URL)
    entry = notes.add_entry("e", DAY, links=[old, new])
    todo = notes.add_todo(entry, "t")
    notes.link_todo(todo, old)
    notes.link_todo(todo, new)
    notes.add_ice("a", DAY, None, [IcedItem(kind="jira.issue", ref_id="APP-1", name="APP-1")])
    notes.add_ice("b", DAY, None, [IcedItem(kind="jira.issue", ref_id=URL, name="APP-1")])
    notes.add_ice("c", DAY, "parked, no branches listed", [])  # an import's, say: it stays

    def identify(kind: str, ref: str) -> str:
        return URL if ref == "APP-1" else ref

    assert notes.rekey(["jira.issue"], identify) == 3  # a link, a todo's link, what's on ice
    [read] = notes.entries()
    assert [link.ref for link in read.links] == [URL]
    assert [link.ref for link in read.todos[0].links] == [URL]
    assert [(ice.title, [item.ref_id for item in ice.items]) for ice in notes.ices()] == [
        ("b", [URL]),  # "a" held only what "b" holds: it goes, not left empty
        ("c", []),
    ]


def test_the_work_notes_other_sections_are_dropped() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:11])
    db.execute("INSERT INTO notes_sections VALUES (0, NULL, 'Intro.')")
    db.migrate("notes", MIGRATIONS)
    assert not db.query("SELECT name FROM sqlite_master WHERE name = 'notes_sections'")
