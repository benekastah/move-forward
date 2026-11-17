"""Changing a work entry on the dashboard, as `mfd notes` can."""

import pytest
from conftest import NOW, entry, panel, stored, todo

from move_forward import refresh
from move_forward.config import Config
from move_forward.db import Database
from move_forward.page import Action, Item
from move_forward.plugin import ActionError
from move_forward.plugins.notes.repository import NotesRepository

PR_URL = "https://github.com/acme/widgets/pull/5"


def _item(config: Config, db: Database) -> Item:
    [item] = panel(refresh.build_dashboard(config, db, NOW), "notes.todos").items
    return item


def _button(item: Item, label: str) -> Action:
    [button] = [a for a in item.actions if a.label == label]
    return button


def test_an_entry_shows_its_status_and_where_it_lives() -> None:
    noted = entry("APP-1 Fix", [todo("a"), todo("b")], Status=["in review"], Where=["engineering"])
    config, db = stored(work_items=[noted])
    assert _item(config, db).details[:2] == ["status: in review", "where: engineering"]


def test_an_entry_sets_its_status_where_it_lives_and_what_blocks_it() -> None:
    config, db = stored(work_items=[entry("APP-1 Fix", [todo("a"), todo("b")])])
    set_ = _button(_item(config, db), "Set")
    assert set_.choices == {"status": "status", "where": "where", "blocked on": "blocked-on"}
    for choice, text in (("status", "in review"), ("where", "engineering"), ("blocked-on", "Sam")):
        notice = refresh.run_action(
            config, db, set_.name, set_.fields | {"input": text, "choice": choice}
        )
        assert notice == f"APP-1 Fix: {choice} is now {text}"
    with pytest.raises(ActionError, match="status, where or blocked-on"):
        refresh.run_action(config, db, set_.name, set_.fields | {"input": "x", "choice": "size"})
    [kept] = NotesRepository(db).entries()
    assert (kept.status, kept.where, kept.blocked_on) == ("in review", "engineering", "Sam")


def test_set_offers_what_the_entry_says_now_and_status_or_where_can_be_cleared() -> None:
    said = {"Status": ["in review"], "Where": ["engineering"]}
    config, db = stored(work_items=[entry("APP-1 Fix", [todo("a"), todo("b")], **said)])
    item = _item(config, db)
    assert _button(item, "Set").suggestions == ["in review", "engineering"]
    for label, field in (("Clear status", "status"), ("Clear where", "where")):
        clear = _button(item, label)
        assert clear.confirm is not None
        assert refresh.run_action(config, db, clear.name, clear.fields) == (
            f"APP-1 Fix: {field} cleared"
        )
    [kept] = NotesRepository(db).entries()
    assert (kept.status, kept.where) == (None, None)
    labels = [a.label for a in _item(config, db).actions]
    assert "Clear status" not in labels and "Clear where" not in labels


def test_a_blocked_entry_can_be_unblocked() -> None:
    blocked = entry("APP-1 Fix", [todo("a"), todo("b")], **{"Blocked on": ["reviews"]})
    config, db = stored(work_items=[blocked])
    unblock = _button(_item(config, db), "Unblock")
    assert unblock.confirm is not None and "reviews" in unblock.confirm
    assert refresh.run_action(config, db, unblock.name, unblock.fields) == (
        "APP-1 Fix: blocked-on cleared"
    )
    assert NotesRepository(db).entries()[0].blocked_on is None
    assert "Unblock" not in [a.label for a in _item(config, db).actions]


def test_an_entry_is_linked_to_a_pr_or_ticket() -> None:
    config, db = stored(work_items=[entry("APP-1 Fix", [todo("a"), todo("b")])])
    link = _button(_item(config, db), "Link entry")
    fields = link.fields | {"input": PR_URL}
    assert refresh.run_action(config, db, link.name, fields) == f"APP-1 Fix: linked {PR_URL}"
    with pytest.raises(ActionError, match="already about"):
        refresh.run_action(config, db, link.name, fields)
    assert [x.ref for x in NotesRepository(db).entries()[0].links] == [PR_URL]


def test_removing_an_entry_asks_first() -> None:
    config, db = stored(work_items=[entry("APP-1 Fix", [todo("a"), todo("b")])])
    remove = _button(_item(config, db), "Remove entry")
    assert remove.confirm is not None and "2 open todos" in remove.confirm
    assert refresh.run_action(config, db, remove.name, remove.fields) == "removed APP-1 Fix"
    assert NotesRepository(db).entries() == []


def test_an_entrys_buttons_are_in_act_now_and_on_each_todo_in_focus_mode() -> None:
    names = {"notes/entry_set", "notes/entry_link", "notes/entry_remove"}
    close_out = entry("APP-1 Fix", [todo("a", "quick"), todo("b", "quick")])
    config, db = stored(work_items=[close_out])
    built = refresh.build_dashboard(config, db, NOW)
    [act] = built.act_now()
    assert names <= {a.name for a in act.actions}
    focused = refresh.build_dashboard(config, db, NOW, surface="focus")
    for _, step in focused.focus_items("notes.todos") or []:
        assert names <= {a.name for a in step.actions}
