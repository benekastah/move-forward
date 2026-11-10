"""One plugin's failing hook stays its own: the page, the graph and the other plugins' refreshes
go on without it."""

import logging
import threading
from typing import Any

import pytest
from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.plugin import ActionError, Context, InputRequest
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.notes.plugin import NotesPlugin
from move_forward.registry import enabled
from move_forward.repositories import SnapshotRepository


@pytest.fixture(autouse=True)
def fresh_log_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_last_logged", {})


def _boom(*_: object, **__: object) -> Any:
    raise RuntimeError("boom")


def _boom_per_item(*_: object) -> Any:
    """An `item_actions` that answers once a page, then fails on each item."""
    return _boom


def test_failing_entities_leave_that_plugin_out_of_the_graph_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config, db = stored(
        github=snapshot(mine=[pr(1)]), local=local_snapshot(branches=[local_branch("me/x")])
    )
    monkeypatch.setattr(GitPlugin, "entities", _boom)
    with caplog.at_level(logging.ERROR):
        refresh.build_dashboard(config, db, NOW)
        graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
        dashboard = refresh.build_dashboard(config, db, NOW)
    assert graph.of_kind("git.branch") == [] and graph.get("pull_request", pr(1).url) is not None
    assert dashboard.panels  # the page still builds
    assert [r.message for r in caplog.records].count("git: entities failed") == 1


def test_failing_panels_show_as_one_failed_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]))
    monkeypatch.setattr(GitHubPlugin, "panels", _boom)
    dashboard = refresh.build_dashboard(config, db, NOW)
    failed = next(p for p in dashboard.panels if p.key == "github.failed")
    assert failed.title == "GitHub (failed)" and failed.items == []
    assert any(p.key.startswith("git.") for p in dashboard.panels)  # the others are there


def test_failing_item_actions_and_load_are_left_out_saying_which_call_failed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]))
    monkeypatch.setattr(NotesPlugin, "item_actions", _boom)
    monkeypatch.setattr(GitPlugin, "item_actions", _boom_per_item)
    monkeypatch.setattr(GitPlugin, "load", _boom)
    with caplog.at_level(logging.ERROR):
        dashboard = refresh.build_dashboard(config, db, NOW)
    assert dashboard.panels
    failed = {r.message for r in caplog.records}
    assert "notes: item_actions (once a page) failed" in failed
    assert "git: item_actions (per item) failed" in failed


def test_an_action_that_raises_says_it_failed_not_that_a_field_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = stored()

    def needs_x(req: InputRequest[Any]) -> str:
        return req.field("x")

    def looks_up(req: InputRequest[Any]) -> str:
        return {"a": "1"}["b"]  # the plugin's own bug

    def actions(self: NotesPlugin) -> dict[str, Any]:
        return {"boom": _boom, "needs": needs_x, "own": looks_up}

    monkeypatch.setattr(NotesPlugin, "actions", actions)

    with pytest.raises(ActionError, match=r"notes/boom failed: RuntimeError: boom"):
        refresh.perform_action(config, db, "notes/boom", {})
    with pytest.raises(ActionError, match="missing field 'x'"):
        refresh.perform_action(config, db, "notes/needs", {})
    with pytest.raises(ActionError, match="notes/own failed: KeyError"):
        refresh.perform_action(config, db, "notes/own", {})


def test_a_collect_past_its_timeout_fails_alone_and_isnt_run_again_until_it_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = stored()
    release = threading.Event()
    calls: list[str] = []

    def stuck(self: GitHubPlugin, ctx: Context[Any]) -> object:
        calls.append("github")
        release.wait(10)
        raise RuntimeError("gave up")

    def quick(self: GitPlugin, ctx: Context[Any]) -> LocalSnapshot:
        calls.append("git")
        return local_snapshot()

    monkeypatch.setattr(GitHubPlugin, "collect", stuck)
    monkeypatch.setattr(refresh, "COLLECT_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(GitPlugin, "collect", quick)
    try:
        refresh.refresh(config, db, NOW)
        sources = SnapshotRepository(db).all()
        assert sources["github"].error == "timed out after 0.1s"
        assert sources["git"].error is None and sources["git"].fetched_at == NOW
        refresh.refresh(config, db, NOW)  # github's first collect hasn't returned
        assert sorted(calls[:2]) == ["git", "github"] and calls[2:] == ["git"]
    finally:
        release.set()
