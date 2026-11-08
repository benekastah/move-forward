"""Starting a todo on the dashboard, with nothing to start it from."""

from datetime import date, timedelta

import pytest
from conftest import NOW, dashboard, entry, panel, stored, todo

from move_forward import localtime, refresh, render
from move_forward.plugin import ActionError
from move_forward.plugins.notes.models import Entry, Todo

NEW = "notes/todo_new"


def test_a_todo_starts_from_nothing_as_an_entry_of_its_own() -> None:
    config, db = stored()
    todos = panel(refresh.build_dashboard(config, db, NOW), "notes.todos")
    [new] = todos.actions
    assert (new.name, new.label, new.choices["Quick"]) == (NEW, "New todo", "quick")
    assert "New todo starts one here" in todos.description
    assert refresh.run_action(config, db, NEW, {"input": "buy milk", "choice": "quick"}) == (
        "added: buy milk"
    )
    assert refresh.run_action(config, db, NEW, {"input": "buy milk"}) == "already a todo: buy milk"
    [item] = panel(refresh.build_dashboard(config, db, NOW), "notes.todos").items
    assert item.title == "buy milk" and item.signals is not None  # quick: a close-out


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (" ", "empty"),
        ("two\nlines", "one line"),
        ("two\u2028lines", "one line"),  # any break splitlines takes
        ("Reviews requested of me", "a section"),
    ],
)
def test_a_todo_the_notes_would_read_back_otherwise_is_refused(text: str, why: str) -> None:
    config, db = stored()
    with pytest.raises(ActionError, match=why):
        refresh.run_action(config, db, NEW, {"input": text})


def test_a_todo_isnt_put_in_an_entry_on_ice() -> None:
    config, db = stored()
    refresh.run_action(config, db, NEW, {"input": "fix the sink"})
    refresh.run_action(config, db, "notes/ice_item", {"kind": "notes.entry", "id": "fix the sink"})
    with pytest.raises(ActionError, match="on ice"):
        refresh.run_action(config, db, NEW, {"input": "fix the sink"})


def test_a_leading_size_sizes_the_new_todo_and_a_bracket_is_kept() -> None:
    config, db = stored()
    refresh.run_action(config, db, NEW, {"input": "[quick] water plants"})
    refresh.run_action(config, db, NEW, {"input": "[docs] fix the typo"})
    items = {
        i.title: i for i in panel(refresh.build_dashboard(config, db, NOW), "notes.todos").items
    }
    assert set(items) == {"water plants", "[docs] fix the typo"}
    assert items["water plants"].signals is not None  # quick: a close-out


def test_add_todo_creating_an_entry_refuses_a_title_the_notes_would_split() -> None:
    config, db = stored()
    fields = {"entry": "a\nb", "create": "1", "input": "x"}
    with pytest.raises(ActionError, match="one line"):
        refresh.run_action(config, db, "notes/todo_add", fields)


def test_add_todo_takes_a_leading_size_as_new_todo_does() -> None:
    config, db = stored()
    refresh.run_action(config, db, NEW, {"input": "chores"})
    fields = {"entry": "chores", "input": "[big] clean the garage"}
    refresh.run_action(config, db, "notes/todo_add", fields)
    [item] = panel(refresh.build_dashboard(config, db, NOW), "notes.todos").items
    assert [(p.title, p.badges[0]) for p in item.parts] == [
        ("chores", "unsized"),
        ("clean the garage", "big"),
    ]


def test_the_size_chosen_wins_over_one_written() -> None:
    config, db = stored()
    refresh.run_action(config, db, NEW, {"input": "[big] water plants", "choice": "quick"})
    [item] = panel(refresh.build_dashboard(config, db, NOW), "notes.todos").items
    assert (item.title, item.signals is not None) == ("water plants", True)  # quick: a close-out


def _with_done(work: Entry, *done: tuple[str, date]) -> Entry:
    ticked = [Todo(id=0, text=text, done_on=on) for text, on in done]
    return work.model_copy(update={"todos": [*work.todos, *ticked]})


def test_a_todo_ticked_off_today_stays_listed_ticked_to_untick() -> None:
    today = localtime.day(NOW)
    built = dashboard(
        work_items=[
            _with_done(entry("Chores", [todo("dishes")]), ("laundry", today)),
            _with_done(entry("Done today", []), ("taxes", today)),
            _with_done(entry("Done before", []), ("old", today - timedelta(days=1))),
        ]
    )
    items = {i.title: i for i in panel(built, "notes.todos").items}
    assert set(items) == {"Chores", "Done today"}  # what was ticked off before is gone
    chores = items["Chores"]
    assert [(p.title, [a.name for a in p.actions if a.checkbox]) for p in chores.parts] == [
        ("dishes", ["notes/todo_done"]),
        ("laundry", ["notes/todo_undone"]),  # ticked: unticking reopens it
    ]
    done_today = items["Done today"]
    assert (done_today.signals, done_today.badges, done_today.done) == (None, [], True)
    shown = render.item_html(done_today)  # its one todo, ticked: reachable to untick
    assert "taxes" in shown and "notes/todo_undone" in shown
    assert "notes/todo_add" in [a.name for a in done_today.actions]  # a follow-up, all the same
    assert chores.details == [] and chores.parts[1].badges == ["done"]
    assert chores.parts[1].subjects == []  # nothing to rank or tag on what's done
    stepped = [part.title for _, part in built.focus_items("notes.todos") or []]
    assert stepped == ["dishes"]  # what's ticked off isn't a step
    assert list(items) == ["Chores", "Done today"]  # all ticked off: last
