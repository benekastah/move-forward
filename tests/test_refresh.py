"""The refresh: plugins' snapshots collected and stored, including those that need the graph."""

from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, issue, local_snapshot, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.act_now import Feedback
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.page import Item
from move_forward.plugin import ActionError, Context
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.jira.repository import IssueRepository
from move_forward.repositories import FeedbackRepository, SnapshotRepository


class _Git(GitPlugin):
    def collect(self, ctx: Context[GitConfig]) -> LocalSnapshot:
        return local_snapshot()


class _JiraAfterGit(JiraPlugin):
    """Fetches an issue per repo git found, and remembers what after_collect saw."""

    collects_after_graph: ClassVar[bool] = True
    seen_by_after_collect: ClassVar[list[str]] = []

    def collect_after(self, ctx: Context[JiraConfig], graph: Graph) -> JiraSnapshot:
        return JiraSnapshot(
            issues=[issue(summary=f"about {r.ref.id}") for r in graph.of_kind("repo")]
        )

    def after_collect(self, ctx: Context[JiraConfig], graph: Graph) -> None:
        self.seen_by_after_collect[:] = [t.ref.id for t in graph.of_kind("ticket")]


@pytest.fixture(autouse=True)
def plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = {"git": _Git(), "jira": _JiraAfterGit()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])


def test_a_plugin_collects_from_the_graph_of_the_others(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "email": "me@x",
            "git": {"repos": []},
            "jira": {"site": "https://j"},
        }
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    stored = IssueRepository(db).load()
    assert [i.summary for i in stored.issues] == ["about acme/widgets"]
    assert _JiraAfterGit.seen_by_after_collect == ["APP-1"]


class _GitInKv(GitPlugin):
    """git, keeping its snapshot in kv (as a stand-in for its own tables), and failing to
    collect once `broken`."""

    broken: ClassVar[bool] = False

    def collect(self, ctx: Context[GitConfig]) -> LocalSnapshot:
        if self.broken:
            raise RuntimeError("git is gone")
        return local_snapshot()

    def store(self, ctx: Context[GitConfig], snapshot: LocalSnapshot) -> None:
        ctx.kv.set("snapshot", snapshot.model_dump_json())

    def load(self, ctx: Context[GitConfig]) -> LocalSnapshot | None:
        stored = ctx.kv.get("snapshot")
        return LocalSnapshot.model_validate_json(stored) if stored else None


def test_a_plugin_can_keep_its_snapshot_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"git": _GitInKv()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = Config.model_validate({"email": "me@x", "git": {"repos": []}})
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    status = SnapshotRepository(db).all()["git"]
    assert status.error is None and status.fetched_at == NOW
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.get("repo", "acme/widgets") is not None
    # A failed collect keeps what the last good one stored, and says why.
    monkeypatch.setattr(_GitInKv, "broken", True)
    refresh.refresh(config, db, NOW)
    assert "git is gone" in (SnapshotRepository(db).all()["git"].error or "")
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.get("repo", "acme/widgets") is not None


class _GitThatCantStore(_GitInKv):
    def store(self, ctx: Context[GitConfig], snapshot: LocalSnapshot) -> None:
        raise RuntimeError("disk full")


def test_a_failing_store_is_recorded_like_a_failing_collect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swap = {"git": _GitThatCantStore()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = Config.model_validate({"email": "me@x", "git": {"repos": []}})
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    assert "disk full" in (SnapshotRepository(db).all()["git"].error or "")


def test_act_now_buttons_snooze_dismiss_pin_and_unpin(monkeypatch: pytest.MonkeyPatch) -> None:
    config, db = stored(
        github=snapshot(direct_requests=[pr(1, author="alice"), pr(2, author="bob")])
    )

    def act() -> list[Item]:
        return refresh.build_dashboard(config, db, NOW).panel("act.now").items

    first, second = act()
    pin = next(a for a in second.actions if a.label == "Pin")
    assert refresh.run_action(config, db, pin.name, pin.fields) == "pinned"
    assert [i.title for i in act()] == [second.title, first.title]
    unpin = next(a for a in act()[0].actions if a.label == "Unpin")
    assert refresh.run_action(config, db, unpin.name, unpin.fields) == "unpinned"
    dismiss = next(a for a in first.actions if a.label == "Dismiss")
    assert refresh.run_action(config, db, dismiss.name, dismiss.fields) == "dismissed"
    snooze = next(a for a in second.actions if a.label == "Snooze")
    assert refresh.run_action(config, db, snooze.name, snooze.fields) == "snoozed until tomorrow"
    assert act() == []
    said = {f.action: f for f in FeedbackRepository(db).all().values()}
    assert set(said) == {"dismiss", "snooze"}
    snoozed_until = said["snooze"].until
    assert snoozed_until is not None and snoozed_until > NOW
    with pytest.raises(ActionError, match="unknown action"):
        refresh.run_action(config, db, "act_now/forget", snooze.fields)
    with pytest.raises(ActionError, match="missing field"):
        refresh.run_action(config, db, "act_now/pin", {})


def test_feedback_that_is_over_or_old_is_pruned() -> None:
    db = Database(":memory:")
    feedback = FeedbackRepository(db)
    feedback.say(("pr", "1", ""), Feedback(action="snooze", fingerprint="f", until=NOW), NOW)
    feedback.say(
        ("pr", "2", ""), Feedback(action="dismiss", fingerprint="f"), NOW - timedelta(days=100)
    )
    feedback.say(("pr", "3", ""), Feedback(action="pin", fingerprint="f"), NOW)
    assert feedback.prune(NOW + timedelta(seconds=1), NOW - timedelta(days=90)) == 2
    assert list(feedback.all()) == [("pr", "3", "")]
