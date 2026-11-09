"""git watching its repos as they change: refs moving and worktrees edited, without polling."""

import json
import sys
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import ChangedRequest, CollectRequest, Context, StoreRequest
from move_forward.plugins.git import watch
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.plugin import MOVED, GitPlugin
from move_forward.plugins.git.repository import MIGRATIONS
from move_forward.repositories import KvRepository
from move_forward.shell import git, git_common_dir

WAIT_SECONDS = 10.0
QUIET_SECONDS = 1.0
"""How long nothing must happen to count as nothing happening."""


def _repo(path: Path) -> Path:
    """A repo tracking `src/deep/a.py`, and ignoring `node_modules/` nested in `src/`."""
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "me@example.com")
    git(path, "config", "user.name", "Me")
    git(path, "config", "commit.gpgsign", "false")
    (path / "src" / "deep").mkdir(parents=True)
    (path / "src" / "deep" / "a.py").write_text("a\n")
    (path / ".gitignore").write_text("node_modules/\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "first")
    (path / "src" / "node_modules" / "dep").mkdir(parents=True)
    return path.resolve()


def _until(seen: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if seen():
            return True
        time.sleep(0.05)
    return False


class _Seen:
    """What a watcher hands over, gathered across `take`s."""

    def __init__(self, watcher: watch.Watcher):
        self._watcher = watcher
        self.moved: set[Path] = set()
        self.edited: set[Path] = set()

    def moves(self, common: Path) -> bool:
        self.moved.clear()
        return _until(lambda: self._took() and common in self.moved)

    def edits(self, worktree: Path) -> bool:
        self.edited.clear()
        return _until(lambda: self._took() and worktree in self.edited)

    def _took(self) -> bool:
        moved, edited = self._watcher.take()
        self.moved |= moved
        self.edited |= edited
        return True


@pytest.fixture
def watcher() -> Iterator[watch.Watcher]:
    found = watch.Watcher()
    yield found
    found.stop()


@pytest.mark.parametrize("per_directory", [True, False])
def test_a_new_worktrees_directory_moves_a_repo_only_where_watched_a_directory_at_a_time(
    monkeypatch: pytest.MonkeyPatch, per_directory: bool
) -> None:
    """On Linux a new directory is watched late, so its HEAD can be written unseen; elsewhere a
    directory is reported changed whenever its index is written."""
    monkeypatch.setattr(watch, "PER_DIRECTORY", per_directory)
    assert watch.moves_repo(Path("worktrees/wt")) is per_directory
    assert watch.moves_repo(Path("worktrees")) is per_directory
    assert watch.moves_repo(Path("worktrees/wt/HEAD"))


def test_refs_head_packed_refs_and_config_move_a_repo() -> None:
    assert watch.moves_repo(Path("refs/heads/me/x"))
    assert watch.moves_repo(Path("HEAD")) and watch.moves_repo(Path("packed-refs"))
    assert watch.moves_repo(Path("worktrees/wt/HEAD"))
    assert not watch.moves_repo(Path("worktrees/wt/index"))
    assert watch.moves_repo(Path("config"))  # e.g. git stack tracking a branch
    assert not watch.moves_repo(Path("index"))
    assert not watch.moves_repo(Path("objects/ab/cdef"))
    assert not watch.moves_repo(Path("worktrees/wt/index"))


def test_tracked_directories_are_those_in_the_index(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / "new" / "pkg").mkdir(parents=True)
    (repo / "new" / "pkg" / "b.py").write_text("b\n")
    assert watch.tracked_dirs(repo) == {repo, repo / "src", repo / "src" / "deep"}
    git(repo, "add", "new")  # staged, not committed
    assert repo / "new" / "pkg" in watch.tracked_dirs(repo)


def test_edits_staging_and_commits_are_seen_as_they_happen(
    tmp_path: Path, watcher: watch.Watcher
) -> None:
    repo = _repo(tmp_path / "r")
    common = repo / ".git"
    watcher.sync({repo: common})
    assert watcher.repos == {common} and watcher.worktrees == {repo}
    seen = _Seen(watcher)

    (repo / "src" / "deep" / "a.py").write_text("changed\n")  # nested in a tracked directory
    assert seen.edits(repo)
    (repo / "src" / "deep" / "c.py").write_text("c\n")
    assert seen.edits(repo)
    git(repo, "add", "src/deep/c.py")  # writes only the index
    assert seen.edits(repo)
    git(repo, "commit", "-q", "-m", "second")
    assert seen.moves(common)
    git(repo, "config", "branch.main.stackParent", "base")  # as git stack tracks a branch
    assert seen.moves(common)
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "wt"))  # no ref moves
    assert seen.moves(common)


def test_a_newly_staged_directory_is_watched_from_the_next_sync(
    tmp_path: Path, watcher: watch.Watcher
) -> None:
    repo = _repo(tmp_path / "r")
    watcher.sync({repo: repo / ".git"})
    (repo / "new").mkdir()
    (repo / "new" / "b.py").write_text("b\n")
    git(repo, "add", "new")
    watcher.sync({repo: repo / ".git"})  # as each refresh does
    seen = _Seen(watcher)
    (repo / "new" / "b.py").write_text("changed\n")
    assert seen.edits(repo)


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="only inotify watches a directory at a time; elsewhere the worktree is one tree,"
    " and test_ignored_files_dont_refresh_the_repo covers every system",
)
def test_ignored_directories_arent_watched(tmp_path: Path, watcher: watch.Watcher) -> None:
    repo = _repo(tmp_path / "r")
    watcher.sync({repo: repo / ".git"})
    (repo / "src" / "node_modules" / "dep" / "index.js").write_text("x\n")
    time.sleep(QUIET_SECONDS)
    assert watcher.take() == (set(), set())


def test_a_worktree_no_longer_found_stops_being_watched(
    tmp_path: Path, watcher: watch.Watcher
) -> None:
    repo = _repo(tmp_path / "r")
    watcher.sync({repo: repo / ".git"})
    watcher.sync({})
    assert watcher.repos == set() and watcher.worktrees == set()
    (repo / "src" / "deep" / "a.py").write_text("changed\n")
    time.sleep(QUIET_SECONDS)
    assert watcher.take() == (set(), set())


def _plugin_context(repo: Path, db: Database) -> Callable[[float], Context[GitConfig]]:
    def ctx(seconds: float) -> Context[GitConfig]:
        return Context(
            config=GitConfig(repos=[repo], status_seconds=0),  # watching alone
            email="me@example.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(seconds=seconds),
            db=db,
            kv=KvRepository(db, "git"),
        )

    return ctx


def test_the_plugin_refreshes_the_repo_an_edit_changed(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin()
    ctx = _plugin_context(repo, db)
    plugin.store(StoreRequest(ctx(0), plugin.collect(CollectRequest(ctx(0)))))
    try:
        assert not plugin.changed(ChangedRequest(ctx(0), Graph([])))  # starts watching
        (repo / "src" / "deep" / "a.py").write_text("changed\n")
        assert _until(lambda: plugin.changed(ChangedRequest(ctx(1), Graph([]))))
        assert KvRepository(db, "git").get(MOVED) == json.dumps([str(git_common_dir(repo))])
    finally:
        plugin.stop_watching()


def test_ignored_files_dont_refresh_the_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin()
    ctx = _plugin_context(repo, db)
    plugin.store(StoreRequest(ctx(0), plugin.collect(CollectRequest(ctx(0)))))
    try:
        assert not plugin.changed(ChangedRequest(ctx(0), Graph([])))  # starts watching
        (repo / "src" / "node_modules" / "dep" / "index.js").write_text("x\n")
        time.sleep(QUIET_SECONDS)
        assert not plugin.changed(
            ChangedRequest(ctx(1), Graph([]))
        )  # git status counts nothing new
    finally:
        plugin.stop_watching()


@pytest.mark.parametrize("only_recursive", [False, True])
def test_a_watch_that_errors_is_polled_and_watched_anew_at_the_next_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, only_recursive: bool
) -> None:
    """Whichever watch dies, the worktrees are polled too: the recursive one (of the git
    directories) is what sees their indexes."""
    repo = _repo(tmp_path / "r")
    real = watch.RustNotify
    failing = [True]

    class Failing:
        def __init__(self, *args: Any) -> None:
            self._real = real(*args)
            self._recursive = bool(args[4])

        def watch(self, *args: Any) -> object:
            if failing[0] and (self._recursive or not only_recursive):
                raise RuntimeError("watcher went away")
            return self._real.watch(*args)

        def close(self) -> None:
            self._real.close()

    monkeypatch.setattr(watch, "RustNotify", Failing)
    watcher = watch.Watcher()
    try:
        watcher.sync({repo: repo / ".git"})
        assert _until(lambda: watcher.repos == set() and watcher.worktrees == set())
        failing[0] = False
        watcher.sync({repo: repo / ".git"})
        assert watcher.repos == {repo / ".git"} and watcher.worktrees == {repo}
    finally:
        watcher.stop()
