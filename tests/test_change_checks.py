"""Core keeping what each plugin's change checks say moved: the fingerprint to compare the
next with, the subjects pending until a collect given them stores, and when the last full
collect was."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import NOW, local_snapshot

from move_forward import refresh, registry
from move_forward.change_checks import ChangeChecks
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Changed, ChangedRequest, CollectRequest
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.registry import Enabled


class _Git(GitPlugin):
    """git whose change check answers from `answers` and whose collect notes what it was
    given, failing while `failing`."""

    def __init__(self) -> None:
        super().__init__(watching=False)
        self.answers: list[Changed] = []
        self.asked: list[ChangedRequest[GitConfig]] = []
        self.given: list[tuple[bool, frozenset[str]]] = []
        self.failing = False

    def changed(self, req: ChangedRequest[GitConfig]) -> Changed:
        self.asked.append(req)
        return self.answers.pop(0) if self.answers else Changed()

    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        self.given.append((req.full, req.subjects))
        if self.failing:
            raise RuntimeError("offline")
        return local_snapshot()


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch) -> _Git:
    plugin = _Git()
    monkeypatch.setattr(registry, "PLUGINS", [plugin])
    return plugin


def _config(path: Path) -> Config:
    return Config.model_validate(
        {"email": "me@x", "git": {"repos": [], "refresh_seconds": 600}, "db_path": path}
    )


def _git(config: Config, db: Database, at: datetime) -> Enabled:
    [e] = registry.enabled(config, db, at)
    return e


def _minutes(n: int) -> datetime:
    return NOW + timedelta(minutes=n)


def test_a_fingerprint_counts_once_it_differs_from_the_last(git: _Git, tmp_path: Path) -> None:
    config, db, checks = _config(tmp_path / "x.db"), Database(":memory:"), ChangeChecks()
    registry.migrate(config, db)
    e = _git(config, db, NOW)
    git.answers[:] = [Changed(fingerprint=f) for f in ("a", "a", "b", "b")]
    assert checks.check(e, Graph()) is None  # the first: nothing to compare with
    assert checks.check(e, Graph()) is None
    assert checks.check(e, Graph()) == Changed(fingerprint="b")
    assert checks.check(e, Graph()) is None
    assert [r.fingerprint for r in git.asked] == [None, "a", "a", "b"]
    assert git.asked[1].checked_at == NOW


def test_what_a_check_kept_survives_a_restart(git: _Git, tmp_path: Path) -> None:
    config, db = _config(tmp_path / "x.db"), Database(tmp_path / "x.db")
    registry.migrate(config, db)
    git.answers[:] = [Changed(fingerprint="a", subjects=frozenset({"r"})), Changed(fingerprint="b")]
    ChangeChecks().check(_git(config, db, NOW), Graph())
    del db
    db = Database(tmp_path / "x.db")
    assert ChangeChecks().check(_git(config, db, NOW), Graph()) == Changed(fingerprint="b")
    assert ChangeChecks().take(_git(config, db, NOW), None).subjects == {"r"}


def test_pending_subjects_ask_for_a_refresh_once_and_go_once_a_collect_given_them_stores(
    git: _Git, tmp_path: Path
) -> None:
    config, db, checks = _config(tmp_path / "x.db"), Database(":memory:"), ChangeChecks()
    registry.migrate(config, db)
    refresh.refresh(config, db, _minutes(0), {"git"}, checks)  # the first: a full collect
    git.answers[:] = [Changed(subjects=frozenset({"a"}))] * 2
    assert checks.check(_git(config, db, _minutes(1)), Graph()) is not None
    assert checks.check(_git(config, db, _minutes(1)), Graph()) is None  # not new: no refresh
    git.failing = True
    refresh.refresh(config, db, _minutes(2), {"git"}, checks)
    git.answers[:] = [Changed(subjects=frozenset({"a"}))]
    # Still pending after a collect that failed, but nothing new moved: no refresh (#192).
    assert checks.check(_git(config, db, _minutes(3)), Graph()) is None
    git.failing = False
    refresh.refresh(config, db, _minutes(4), {"git"}, checks)
    assert git.given == [(True, frozenset()), (False, {"a"}), (False, {"a"})]
    e = _git(config, db, _minutes(5))
    assert checks.take(e, None).subjects == set()  # gone once a collect given it stored
    git.answers[:] = [Changed(subjects=frozenset({"c"})), Changed(subjects=frozenset({"d"}))]
    checks.check(e, Graph())
    taken = checks.take(e, None)
    checks.check(e, Graph())  # while it collects
    checks.stored(e, taken)
    assert checks.take(e, None).subjects == {"d"}


def test_what_moves_while_a_collect_runs_is_kept_and_what_moved_before_it_isnt_a_change(
    git: _Git, tmp_path: Path
) -> None:
    config, db, checks = _config(tmp_path / "x.db"), Database(":memory:"), ChangeChecks()
    registry.migrate(config, db)
    git.answers[:] = [Changed(fingerprint="a")]
    assert checks.check(_git(config, db, NOW), Graph()) is None
    # It moved to b, and then a collect started: it read b, so b is no change.
    git.answers[:] = [Changed(fingerprint="b"), Changed(fingerprint="b")]
    refresh.refresh(config, db, NOW, {"git"}, checks)
    assert checks.check(_git(config, db, NOW), Graph()) is None
    # It moves to c after a collect started at b: the next check sees it.
    git.answers[:] = [Changed(fingerprint="b"), Changed(fingerprint="c")]
    refresh.refresh(config, db, NOW, {"git"}, checks)
    assert checks.check(_git(config, db, NOW), Graph()) == Changed(fingerprint="c")


def test_a_collect_is_full_when_due_when_something_unnamed_moved_or_nothing_is_pending(
    git: _Git, tmp_path: Path
) -> None:
    config, db, checks = _config(tmp_path / "x.db"), Database(":memory:"), ChangeChecks()
    registry.migrate(config, db)

    def collect(minutes: int, *answers: Changed) -> tuple[bool, frozenset[str]]:
        git.answers[:] = list(answers)
        refresh.refresh(config, db, _minutes(minutes), {"git"}, checks)
        return git.given[-1]

    a = Changed(subjects=frozenset({"a"}))
    assert collect(0, a) == (True, {"a"})  # never collected in full
    assert collect(1, a) == (False, {"a"})
    assert collect(2) == (True, frozenset())  # nothing pending: a Refresh button
    assert collect(3, Changed(subjects=frozenset({"a"}), everything=True)) == (True, {"a"})
    git.failing = True
    assert collect(13, a) == (True, {"a"})  # refresh_seconds since the last full one
    git.failing = False
    assert collect(14, a) == (True, {"a"})  # that one failed: it isn't counted
    assert collect(15, a) == (False, {"a"})


def test_without_the_servers_checks_a_refresh_asks_nothing_but_gives_what_is_pending(
    git: _Git, tmp_path: Path
) -> None:
    config, db, checks = _config(tmp_path / "x.db"), Database(":memory:"), ChangeChecks()
    registry.migrate(config, db)
    refresh.refresh(config, db, _minutes(0), {"git"}, checks)
    git.answers[:] = [Changed(subjects=frozenset({"a"}))]
    checks.check(_git(config, db, _minutes(1)), Graph())
    asked = len(git.asked)
    refresh.refresh(config, db, _minutes(2), {"git"})  # `mfd refresh`
    assert len(git.asked) == asked and git.given[-1] == (False, {"a"})
