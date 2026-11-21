"""Holding my PRs with merge conditions, and releasing them, from their items on the page."""

import pytest
from conftest import NOW, approved_review, entry, panel, pr, snapshot, stored

from move_forward import refresh
from move_forward.config import Config
from move_forward.db import Database
from move_forward.page import Action, Item
from move_forward.plugin import ActionError
from move_forward.plugins.notes.models import Condition
from move_forward.plugins.notes.repository import NotesRepository

READY = pr(1, reviews=[approved_review()], review_decision="APPROVED")


def _item(config: Config, db: Database, key: str) -> Item:
    built = refresh.build_dashboard(config, db, NOW)
    [item] = panel(built, key).items
    return item


def _button(item: Item, label: str) -> Action:
    [button] = [a for a in item.actions if a.label == label]
    return button


def test_hold_adds_a_merge_condition_to_the_prs_entry() -> None:
    noted = entry("APP-1 Fix", [], Where=[READY.url])
    config, db = stored(github=snapshot(mine=[READY]), work_items=[noted])
    hold = _button(_item(config, db, "github.ready_to_merge"), "Hold")
    notice = refresh.run_action(config, db, hold.name, hold.fields | {"input": "after the release"})
    assert notice == "held PR 1: after the release"
    [kept] = NotesRepository(db).entries()
    assert kept.conditions == [Condition(pr=READY.url, text="after the release")]
    [held] = panel(refresh.build_dashboard(config, db, NOW), "github.held").items
    assert held.details == ["held: after the release"]
    with pytest.raises(ActionError, match="already"):
        refresh.run_action(config, db, hold.name, hold.fields | {"input": "after the release"})


def test_hold_makes_an_entry_for_a_pr_without_one() -> None:
    config, db = stored(github=snapshot(mine=[READY]))
    hold = _button(_item(config, db, "github.ready_to_merge"), "Hold")
    refresh.run_action(config, db, hold.name, hold.fields | {"input": "after the release"})
    [made] = NotesRepository(db).entries()
    assert made.title == f"{READY.url} {READY.title}"
    assert made.conditions == [Condition(pr=READY.url, text="after the release")]


def test_release_removes_the_condition_chosen_asking_first() -> None:
    conditions = {"Merge conditions": ["after the release", "ask Sam"], "Where": [READY.url]}
    config, db = stored(
        github=snapshot(mine=[READY]), work_items=[entry("APP-1 Fix", [], **conditions)]
    )
    release = _button(_item(config, db, "github.held"), "Release")
    assert release.choices == {"after the release": "after the release", "ask Sam": "ask Sam"}
    assert release.confirm is not None
    notice = refresh.run_action(config, db, release.name, release.fields | {"choice": "ask Sam"})
    assert notice == "released PR 1 from: ask Sam"
    assert NotesRepository(db).entries()[0].conditions == [
        Condition(pr=READY.url, text="after the release")
    ]
    with pytest.raises(ActionError, match="no merge condition"):
        refresh.run_action(config, db, release.name, release.fields | {"choice": "ask Sam"})


def test_a_pr_not_held_has_no_release() -> None:
    config, db = stored(github=snapshot(mine=[READY]))
    assert "Release" not in [a.label for a in _item(config, db, "github.ready_to_merge").actions]
