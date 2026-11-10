"""Fetching some entities again, through the plugins that publish them, instead of
refreshing the plugins whole."""

import time
from typing import ClassVar

import pytest
from conftest import NOW, issue, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.graph import Ref
from move_forward.plugin import ContextRequest, RefreshRequest
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import SnapshotRepository

PR_1 = Ref(kind="pull_request", id=pr(1).url)
APP_1 = Ref(kind="ticket", id="https://jira/browse/APP-1")
ASKED: list[tuple[str, bool, frozenset[str]]] = []
"""What each recording plugin's collect was asked: by whom, whether in full, and which
subjects."""


class _GitHub(GitHubPlugin):
    """github, recording what it's asked to fetch and keeping its snapshot."""

    slow: ClassVar[bool] = False

    def collect(self, req: RefreshRequest[GitHubConfig]) -> GitHubSnapshot:
        ASKED.append((self.name, req.full, req.subjects))
        if self.slow:
            time.sleep(0.5)
        kept = self.load(ContextRequest(req.ctx))
        assert kept is not None
        return kept


class _Jira(JiraPlugin):
    """jira, recording what it's asked to fetch and keeping its snapshot."""

    def collect(self, req: RefreshRequest[JiraConfig]) -> JiraSnapshot:
        ASKED.append((self.name, req.full, req.subjects))
        kept = self.load(ContextRequest(req.ctx))
        assert kept is not None
        return kept


@pytest.fixture(autouse=True)
def recording(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = {"github": _GitHub(), "jira": _Jira()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    ASKED.clear()


def test_each_plugin_collects_just_its_own_by_id() -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    unknown = Ref(kind="pull_request", id="https://github.com/acme/widgets/pull/99")
    assert refresh.refresh_subjects(config, db, [PR_1, APP_1, unknown], NOW) == set()
    # Not a PR the graph doesn't have: no one publishes it.
    assert sorted(ASKED) == [
        ("github", False, frozenset({PR_1.id})),
        ("jira", False, frozenset({APP_1.id})),
    ]


def test_it_isnt_the_plugins_refresh() -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]))
    before = SnapshotRepository(db).of(["github"])["github"].fetched_at
    refresh.refresh_subjects(config, db, [PR_1], NOW.replace(year=2030))
    assert SnapshotRepository(db).of(["github"])["github"].fetched_at == before


def test_a_plugin_taking_too_long_is_refreshed_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_GitHub, "slow", True)
    monkeypatch.setattr(refresh, "COLLECT_TIMEOUT_SECONDS", 0.05)
    # Its thread runs on after the test: it mustn't leave github collecting for the next.
    monkeypatch.setattr(refresh, "_collecting", set[str]())
    config, db = stored(github=snapshot(mine=[pr(1)]))
    assert refresh.refresh_subjects(config, db, [PR_1], NOW) == {"github"}
