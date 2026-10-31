"""The refresh: plugins' snapshots collected and stored, including those that need the graph."""

from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, issue, local_snapshot

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import JiraSnapshot, LocalSnapshot
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import SnapshotRepository


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
            "notes": {"dir": str(tmp_path)},
        }
    )
    db = Database(":memory:")
    refresh.refresh(config, db, NOW)
    stored = SnapshotRepository(db).get("jira", JiraSnapshot)
    assert stored is not None
    assert [i.summary for i in stored.issues] == ["about acme/widgets"]
    assert _JiraAfterGit.seen_by_after_collect == ["APP-1"]
