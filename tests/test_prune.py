"""Core's daily prune: when it runs the plugins' prunes, and what it prunes of its own."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import entry, pr, snapshot, stored

from move_forward import prune, refresh
from move_forward.db import Database
from move_forward.graph import Ref
from move_forward.plugin import Context, PruneRequest, SourceError
from move_forward.plugins.notes.config import NotesConfig
from move_forward.plugins.notes.plugin import NotesPlugin
from move_forward.registry import enabled
from move_forward.repositories import ProblemRepository, TagRepository

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


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


def test_a_failing_fetch_in_cores_clean_up_leaves_the_rest_of_it_and_the_plugins_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]), work_items=[entry("Chores", [])])
    gone = Ref(kind="notes.entry", id="Gone")
    TagRepository(db).add(gone, "chores", NOW)
    ran: list[str] = []

    def failing(repo: object, graph: object) -> int:
        raise SourceError("the token can't fetch")

    def notes_prune(self: NotesPlugin, req: PruneRequest[NotesConfig]) -> list[str]:
        ran.append("notes")
        return []

    monkeypatch.setattr(refresh.priority, "forget_finished", failing)
    monkeypatch.setattr(NotesPlugin, "prune", notes_prune)
    plugins = enabled(config, db, NOW)
    graph, _ = refresh.stored_graph(db, plugins)
    notices = refresh._prune_plugins(plugins, graph, db, NOW, 30)  # pyright: ignore[reportPrivateUsage]
    assert "tags: forgot what I tagged or untagged on 1 gone entity(ies)" in notices
    assert ran == ["notes"]


def test_problems_no_plugin_has_reported_for_prune_days_are_forgotten(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config, db = stored(github=snapshot())

    def github_at(days_ago: int) -> Context[Any]:
        then = NOW - timedelta(days=days_ago)
        return next(e for e in enabled(config, db, then) if e.name == "github").ctx

    github_at(40).problem("renamed", "a key a later version no longer reports")
    github_at(40).problem("lasting", "still wrong")
    github_at(10).problem("lasting", "still wrong")  # unchanged, but seen again
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [
        "a key a later version no longer reports",
        "still wrong",
    ]  # seen again isn't logged again
    ProblemRepository(db).set("jira", "team", "no team", NOW - timedelta(days=40))  # removed
    plugins = enabled(config, db, NOW)
    graph, _ = refresh.stored_graph(db, plugins)
    notices = refresh._prune_plugins(plugins, graph, db, NOW, 30)  # pyright: ignore[reportPrivateUsage]
    assert "problems: forgot 2 not reported for 30 days" in notices
    assert ProblemRepository(db).of(["github", "jira"]) == {"github": ["still wrong"]}
