"""The notes plugin's tables, through its repository."""

from datetime import date

import pytest

from move_forward.db import Database
from move_forward.plugins.notes.models import (
    Condition,
    Declined,
    Entry,
    Ice,
    IcedItem,
    Link,
    Section,
    Todo,
)
from move_forward.plugins.notes.repository import MIGRATIONS, NotesRepository

DAY = date(2026, 10, 30)
PR = Link(kind="pr", ref="https://github.com/acme/widgets/pull/1")


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
            kind="branch", ref_id="/src/w\tme/bottom", name="me/bottom", repo_dir="/src/w", sha="a1"
        ),
        IcedItem(
            kind="branch", ref_id="/src/w\tme/top", name="me/top", repo_dir="/src/w", sha="b2"
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


def test_replace_all_keeps_everything_an_import_read(notes: NotesRepository) -> None:
    notes.add_entry("old", DAY)
    entry = Entry(
        id=0,
        title="APP-1 Fix it",
        status="in review",
        where="engineering https://github.com/acme/widgets/pull/1",
        blocked_on="reviews",
        updated_on=DAY,
        todos=[
            Todo(id=0, text="reply", size="quick", links=[PR]),
            Todo(id=0, text="x", done_on=DAY),
        ],
        conditions=[Condition(pr=PR.ref, text="after PR 2")],
        declined=[Declined(on=DAY, text="don't nudge")],
        lines=["- Odd: kept as is"],
        links=[PR],
    )
    ice = Ice(
        id=0,
        title="me/b",
        iced_on=DAY,
        items=[IcedItem(kind="branch", ref_id="/w\tme/b", name="me/b", repo_dir="/w")],
    )
    sections = [Section(heading=None, body="Intro."), Section(heading="Reviews", body="- x")]
    notes.replace_all([entry], [ice], sections)
    [read] = notes.entries()
    assert read.model_copy(update={"id": 0, "todos": []}) == entry.model_copy(update={"todos": []})
    assert [(t.text, t.size, t.done_on, t.links) for t in read.todos] == [
        ("reply", "quick", None, [PR]),
        ("x", None, DAY, []),
    ]
    assert [i.title for i in notes.ices()] == ["me/b"] and notes.sections() == sections


def test_branches_iced_before_names_were_kept_get_theirs_from_their_ids() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:2])
    db.execute("INSERT INTO notes_ice (id, title, iced_on) VALUES (1, 'me/b', '2026-10-01')")
    db.execute("INSERT INTO notes_iced VALUES (1, 0, 'branch', ?, 'a1')", ("/src/w\tme/b",))
    db.migrate("notes", MIGRATIONS)
    [ice] = NotesRepository(db).ices()
    assert ice.items == [
        IcedItem(kind="branch", ref_id="/src/w\tme/b", name="me/b", repo_dir="/src/w", sha="a1")
    ]


def test_an_import_replaces_stacks_on_ice_but_not_what_was_iced_on_its_own(
    notes: NotesRepository,
) -> None:
    stack = [IcedItem(kind="branch", ref_id="/w\tme/a", name="me/a", repo_dir="/w")]
    ticket = [IcedItem(kind="ticket", ref_id="APP-1", name="APP-1 Fix")]
    notes.add_ice("me/a", DAY, None, stack)
    notes.add_ice("APP-1 Fix", DAY, None, ticket)
    imported = Ice(
        id=0,
        title="me/b",
        iced_on=DAY,
        items=[stack[0].model_copy(update={"ref_id": "/w\tme/b", "name": "me/b"})],
    )
    notes.replace_all([], [imported], [])
    assert [i.title for i in notes.ices()] == ["APP-1 Fix", "me/b"]


def test_check_ins_written_as_declines_move_into_their_fields() -> None:
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
    [entry] = NotesRepository(db).entries()
    assert (entry.checked_on, entry.asked_done_on) == (date(2026, 10, 5), date(2026, 10, 3))
    assert entry.declined == [Declined(on=date(2026, 10, 4), text="not today")]


def test_conditions_kept_before_they_named_their_pr_are_given_one_once() -> None:
    db = Database(":memory:")
    db.migrate("notes", MIGRATIONS[:-1])  # before conditions named their PR
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
    assert NotesRepository(db).entries() == [first, second]  # once
