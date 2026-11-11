"""Fetching some entities again: a refresh of just the plugins that publish them, each
narrowed to them."""

import time
from datetime import timedelta
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
LATER = NOW + timedelta(minutes=1)
"""Soon after the first refresh, at `NOW`: no full one is due yet."""


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

    failing: ClassVar[bool] = False

    def collect(self, req: RefreshRequest[JiraConfig]) -> JiraSnapshot:
        ASKED.append((self.name, req.full, req.subjects))
        if self.failing:
            raise RuntimeError("Jira is down")
        kept = self.load(ContextRequest(req.ctx))
        assert kept is not None
        return kept


@pytest.fixture(autouse=True)
def recording(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = {"github": _GitHub(), "jira": _Jira()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    ASKED.clear()


def _refreshed() -> tuple[refresh.Config, refresh.Database]:
    """github and jira, each refreshed in full once, at `NOW`."""
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    refresh.refresh(config, db, NOW)
    ASKED.clear()
    return config, db


def test_each_plugin_collects_just_its_own_by_id() -> None:
    config, db = _refreshed()
    unknown = Ref(kind="pull_request", id="https://github.com/acme/widgets/pull/99")
    refresh.refresh_subjects(config, db, [PR_1, APP_1, unknown], LATER)
    # Not a PR the graph doesn't have: no one publishes it.
    assert sorted(ASKED) == [
        ("github", False, frozenset({PR_1.id})),
        ("jira", False, frozenset({APP_1.id})),
    ]


def test_it_is_the_plugins_refresh_recorded_as_any() -> None:
    config, db = _refreshed()
    SnapshotRepository(db).save_error("jira", NOW, "RuntimeError: Jira is down")
    refresh.refresh_subjects(config, db, [APP_1], LATER)
    status = SnapshotRepository(db).of(["jira", "github"])
    assert (status["jira"].fetched_at, status["jira"].error) == (LATER, None)
    assert status["github"].fetched_at == NOW  # not asked for: not refreshed


def test_a_plugin_failing_is_recorded_as_failed_and_not_collected_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, db = _refreshed()
    monkeypatch.setattr(_Jira, "failing", True)
    refresh.refresh_subjects(config, db, [APP_1], LATER)
    assert list(ASKED) == [("jira", False, frozenset({APP_1.id}))]
    status = SnapshotRepository(db).of(["jira"])["jira"]
    assert (status.fetched_at, status.error) == (LATER, "RuntimeError: Jira is down")


def test_a_plugin_taking_too_long_is_recorded_as_timed_out(monkeypatch: pytest.MonkeyPatch) -> None:
    config, db = _refreshed()
    monkeypatch.setattr(_GitHub, "slow", True)
    monkeypatch.setattr(refresh, "COLLECT_TIMEOUT_SECONDS", 0.05)
    # Its thread runs on after the test: it mustn't leave github collecting for the next.
    monkeypatch.setattr(refresh, "_collecting", set[str]())
    refresh.refresh_subjects(config, db, [PR_1], LATER)
    assert list(ASKED) == [("github", False, frozenset({PR_1.id}))]
    assert SnapshotRepository(db).of(["github"])["github"].error == "timed out after 0.05s"


def test_a_plugin_whose_full_refresh_is_due_refreshes_in_full() -> None:
    config, db = _refreshed()
    refresh.refresh_subjects(config, db, [PR_1], NOW + timedelta(days=1))
    assert list(ASKED) == [("github", True, frozenset({PR_1.id}))]
