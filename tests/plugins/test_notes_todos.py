"""Starting a todo on the dashboard, with nothing to start it from."""

import pytest
from conftest import NOW, panel, stored

from move_forward import refresh
from move_forward.plugin import ActionError

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
        ("Reviews requested of me", "a section"),
        ("[quick] x", "todo's size"),
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
