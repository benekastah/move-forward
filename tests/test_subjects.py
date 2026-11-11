"""Fetching some entities again: a refresh of just the plugins that publish them, each
narrowed to them, unless its refresh was asked for by name too."""

import time
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, issue, pr, snapshot, stored

from move_forward import cli, refresh, registry, server
from move_forward.change_checks import ChangeChecks
from move_forward.config import load
from move_forward.db import Database
from move_forward.graph import Ref
from move_forward.plugin import Changed, ContextRequest, RefreshRequest
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import ChangeRepository, SnapshotRepository

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

    def changed(self, req: RefreshRequest[GitHubConfig]) -> Changed:
        return Changed()  # nothing moved, and nothing asked of GitHub


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


def test_a_plugin_also_asked_for_by_name_is_refreshed_as_it_would_have_been() -> None:
    """Its Refresh button, its own action or its schedule: in full, given what was asked too,
    while one refreshed for what was asked alone is narrowed to it."""
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    checks = ChangeChecks()
    refresh.refresh(config, db, NOW, None, checks)
    ASKED.clear()
    asked = {"github": frozenset({PR_1.id}), "jira": frozenset({APP_1.id})}
    refresh.refresh(config, db, LATER, {"github"}, checks, asked)
    assert sorted(ASKED) == [
        ("github", True, frozenset({PR_1.id})),
        ("jira", False, frozenset({APP_1.id})),
    ]


@pytest.mark.parametrize(
    ("named", "full"),
    [
        (set[str](), False),  # ↻ alone: narrowed to the PR, a full collect being recent
        ({"github"}, True),  # its Refresh button too: in full, its first since the start
    ],
)
def test_a_plugins_first_collect_since_the_start_is_narrowed_only_if_asked_for_alone(
    named: set[str], full: bool
) -> None:
    """A fresh start (new `ChangeChecks`), with a full collect stored before it, within
    github's `refresh_seconds`, and a change still pending."""
    config, db = _refreshed()
    held = pr(2).url
    ChangeRepository(db).checked("github", None, NOW, {held}, everything=False)
    asked = {"github": frozenset({PR_1.id})}
    refresh.refresh(config, db, LATER, named, ChangeChecks(), asked)
    assert list(ASKED) == [("github", full, frozenset({held, PR_1.id}))]


@pytest.mark.parametrize(
    ("named", "full"),
    [
        (set[str](), False),  # ↻ alone: narrowed to the PR
        ({"github"}, True),  # its Refresh button too: in full, as the button would have it
        (None, True),  # the header's Refresh: every plugin by name, so in full
    ],
)
def test_the_loop_narrows_a_plugin_asked_for_entities_unless_its_refresh_was_pressed_too(
    named: set[str] | None, full: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One round of the server's loop, with a ↻ on github's PR pending, and its Refresh, the
    header's, or neither pressed: github is collected once."""
    config, db = stored(github=snapshot(mine=[pr(1)]))
    app = server.App(config, db, "token")
    rounds = [0]

    def wait(timeout: float | None = None) -> bool:
        rounds[0] += 1
        if rounds[0] > 1:
            raise KeyboardInterrupt  # one round after the first refresh is enough
        ASKED.clear()
        app.request_refresh(named)
        app.request_refresh_subjects([PR_1])
        return True

    monkeypatch.setattr(app._wake, "wait", wait)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(KeyboardInterrupt):
        app.run_refresh_loop()
    assert list(ASKED) == [("github", full, frozenset({PR_1.id}))]


def test_mfd_refresh_subject_fetches_just_that(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    path.write_text(
        f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n[github]\norg = "acme"\n',
        encoding="utf-8",
    )
    db = Database(db_path)
    registry.migrate(load(path), db)
    PrRepository(db).replace(snapshot(mine=[pr(1)]))
    assert cli.main(["-c", str(path), "refresh"]) == 0  # in full: the first
    ASKED.clear()
    capsys.readouterr()
    assert cli.main(["-c", str(path), "refresh", "--subject", PR_1.kind, PR_1.id]) == 0
    assert list(ASKED) == [("github", False, frozenset({PR_1.id}))]  # once, and narrowed
    assert "github: ok" in capsys.readouterr().out
