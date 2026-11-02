"""git watching its repos as they change: refs moving and worktrees edited, without polling."""

import json
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.git import changes, watch
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.plugin import MOVED, GitPlugin
from move_forward.plugins.git.repository import MIGRATIONS
from move_forward.repositories import KvRepository
from move_forward.shell import git

WAIT_SECONDS = 10.0
QUIET_SECONDS = 1.0
"""How long nothing must happen to count as nothing happening."""


def _repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "me@example.com")
    git(path, "config", "user.name", "Me")
    git(path, "config", "commit.gpgsign", "false")
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("a\n")
    (path / ".gitignore").write_text("node_modules/\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "first")
    (path / "node_modules" / "dep").mkdir(parents=True)
    return path.resolve()


def _until(seen: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if seen():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def watcher() -> Iterator[watch.Watcher]:
    found = watch.Watcher()
    yield found
    found.stop()


def test_refs_head_packed_refs_and_config_move_a_repo() -> None:
    assert watch.moves_repo(Path("refs/heads/me/x"))
    assert watch.moves_repo(Path("HEAD")) and watch.moves_repo(Path("packed-refs"))
    assert watch.moves_repo(Path("worktrees/wt/HEAD"))
    assert watch.moves_repo(Path("config"))  # e.g. git-stack tracking a branch
    assert not watch.moves_repo(Path("index"))
    assert not watch.moves_repo(Path("objects/ab/cdef"))
    assert not watch.moves_repo(Path("worktrees/wt/index"))


def test_edits_and_commits_are_seen_as_they_happen(tmp_path: Path, watcher: watch.Watcher) -> None:
    repo = _repo(tmp_path / "r")
    common = repo / ".git"
    watcher.sync({repo: common})
    assert watcher.repos == {common} and watcher.worktrees == {repo}
    assert watcher.take() == (set(), set())

    (repo / "node_modules" / "dep" / "index.js").write_text("x\n")  # ignored: not watched
    time.sleep(QUIET_SECONDS)
    assert watcher.take() == (set(), set())

    edited: set[Path] = set()

    def took_edit() -> bool:
        edited.update(watcher.take()[1])
        return repo in edited

    (repo / "src" / "a.py").write_text("changed\n")
    assert _until(took_edit)
    moved: set[Path] = set()

    def took_move() -> bool:
        moved.update(watcher.take()[0])
        return common in moved

    git(repo, "commit", "-q", "-am", "second")
    assert _until(took_move)
    moved.clear()
    git(repo, "config", "branch.main.stackParent", "base")  # as git-stack tracks a branch
    assert _until(took_move)
    moved.clear()  # a first worktree, detached: no ref moves
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt"))
    assert _until(took_move)


def test_a_worktree_no_longer_found_stops_being_watched(
    tmp_path: Path, watcher: watch.Watcher
) -> None:
    repo = _repo(tmp_path / "r")
    watcher.sync({repo: repo / ".git"})
    watcher.sync({})
    assert watcher.repos == set() and watcher.worktrees == set()
    (repo / "src" / "a.py").write_text("changed\n")
    time.sleep(QUIET_SECONDS)
    assert watcher.take() == (set(), set())


def test_the_plugin_refreshes_the_repo_an_edit_changed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin()
    kv = KvRepository(db, "git")

    def ctx(seconds: float) -> Context[GitConfig]:
        return Context(
            config=GitConfig(repos=[repo], status_seconds=0),  # watching alone
            email="me@example.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(seconds=seconds),
            db=db,
            kv=kv,
        )

    plugin.store(ctx(0), plugin.collect(ctx(0)))
    try:
        assert not plugin.changed(ctx(0), Graph([]))  # starts watching
        (repo / "src" / "a.py").write_text("changed\n")
        assert _until(lambda: plugin.changed(ctx(1), Graph([])))
        assert kv.get(MOVED) == json.dumps([str(changes.common_dir(repo))])
    finally:
        plugin.stop_watching()
