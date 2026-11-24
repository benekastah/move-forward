"""git watching its repos as they change: refs moving and worktrees edited, without polling."""

import sys
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW
from git_client.testing import GitCall

from move_forward.api.git import GitCache, git_common_dir
from move_forward.change_checks import ChangeChecks
from move_forward.db import Database
from move_forward.git import Git
from move_forward.graph import Graph
from move_forward.plugin import Context, ContextRequest, RefreshRequest
from move_forward.plugins.git import client, watch
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git.repository import MIGRATIONS
from move_forward.registry import Enabled
from move_forward.repositories import ChangeRepository, KvRepository

WAIT_SECONDS = 10.0
QUIET_SECONDS = 1.0
"""How long nothing must happen to count as nothing happening."""


def _repo(path: Path) -> Path:
    """A repo tracking `src/deep/a.py`, and ignoring `node_modules/` nested in `src/`."""
    path.mkdir()
    Git(path).run("init", "-q", "-b", "main")
    (path / "src" / "deep").mkdir(parents=True)
    (path / "src" / "deep" / "a.py").write_text("a\n")
    (path / ".gitignore").write_text("node_modules/\n")
    Git(path).run("add", ".")
    Git(path).run("commit", "-q", "-m", "first")
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
    Git(repo).run("add", "new")  # staged, not committed
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
    Git(repo).run("add", "src/deep/c.py")  # writes only the index
    assert seen.edits(repo)
    Git(repo).run("commit", "-q", "-m", "second")
    assert seen.moves(common)
    Git(repo).run("config", "branch.main.stackParent", "base")  # as git stack tracks a branch
    assert seen.moves(common)
    Git(repo).run("worktree", "add", "-q", "--detach", str(tmp_path / "wt"))  # no ref moves
    assert seen.moves(common)


def test_a_newly_staged_directory_is_watched_from_the_next_sync(
    tmp_path: Path, watcher: watch.Watcher
) -> None:
    repo = _repo(tmp_path / "r")
    watcher.sync({repo: repo / ".git"})
    (repo / "new").mkdir()
    (repo / "new" / "b.py").write_text("b\n")
    Git(repo).run("add", "new")
    watcher.sync({repo: repo / ".git"})  # as each refresh does
    seen = _Seen(watcher)
    (repo / "new" / "b.py").write_text("changed\n")
    assert seen.edits(repo)


def test_refs_keep_moving_after_a_linked_worktree_is_replaced(
    tmp_path: Path, watcher: watch.Watcher
) -> None:
    repo, linked = _repo(tmp_path / "r"), tmp_path / "linked"
    Git(repo).run("worktree", "add", "-q", "--detach", str(linked))
    common = repo / ".git"
    watcher.sync({repo: common, linked: common})
    linked.rename(tmp_path / "old")
    _repo(linked)
    assert watcher.take()[0] == {common}

    # Deliver the ref event after invalidation, while the old watch is still reporting changes.
    watcher._note([common / "refs" / "heads" / "main"])  # pyright: ignore[reportPrivateUsage]
    assert watcher.take()[0] == {common}


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


def _plugin_context(
    repo: Path, db: Database, *, config: GitConfig | None = None
) -> Callable[[float], Context[GitConfig]]:
    def ctx(seconds: float) -> Context[GitConfig]:
        return Context(
            config=config if config is not None else GitConfig(repos=[repo], status_seconds=0),
            email="me@example.com",
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
    plugin, checks = GitPlugin(), ChangeChecks()
    ctx = _plugin_context(repo, db)
    plugin.snapshots(ContextRequest(ctx(0))).save(plugin.collect(RefreshRequest(ctx(0), Graph())))
    try:
        assert checks.check(Enabled(plugin, ctx(0)), Graph([])) is None  # starts watching
        (repo / "src" / "deep" / "a.py").write_text("changed\n")
        assert _until(lambda: checks.check(Enabled(plugin, ctx(1)), Graph([])) is not None)
        assert ChangeRepository(db).get("git").subjects == {str(git_common_dir(Git(repo)))}
    finally:
        plugin.stop_watching()


def test_change_poll_uses_the_watch_for_known_checkouts(
    tmp_path: Path, git_calls: list[GitCall]
) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin()
    ctx = _plugin_context(repo, db)
    plugin.snapshots(ContextRequest(ctx(0))).save(plugin.collect(RefreshRequest(ctx(0), Graph())))
    try:
        plugin.changed(RefreshRequest(ctx(0), Graph()))  # starts watching
        git_calls.clear()
        plugin.changed(RefreshRequest(ctx(2), Graph()))
        assert not git_calls
    finally:
        plugin.stop_watching()


@pytest.mark.parametrize("watching", [False, True])
def test_edits_in_a_linked_worktree_are_seen_when_the_main_lookup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, watching: bool
) -> None:
    main, linked = _repo(tmp_path / "main"), tmp_path / "linked"
    Git(main).run("worktree", "add", "-q", "--detach", str(linked))
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin, checks = GitPlugin(watching=watching), ChangeChecks()
    config = GitConfig(repos=[main, linked], status_seconds=0 if watching else 1)
    ctx = _plugin_context(main, db, config=config)
    plugin.snapshots(ContextRequest(ctx(0))).save(plugin.collect(RefreshRequest(ctx(0), Graph())))
    common = git_common_dir(Git(main))
    lookup = client.common_dir

    def common_dir(path: Path, cache: GitCache) -> Path | None:
        return None if path == main else lookup(path, cache)

    monkeypatch.setattr(client, "common_dir", common_dir)
    try:
        assert checks.check(Enabled(plugin, ctx(0)), Graph([])) is None
        (linked / "src" / "deep" / "a.py").write_text("changed\n", encoding="utf-8")

        def changed() -> bool:
            answer = checks.check(Enabled(plugin, ctx(1)), Graph([]))
            return answer is not None and answer.subjects == frozenset({str(common)})

        assert _until(changed) if watching else changed()
    finally:
        plugin.stop_watching()


@pytest.mark.parametrize("replace_checkout", [False, True])
def test_the_plugin_detects_a_repo_replaced_at_its_watched_path(
    tmp_path: Path, replace_checkout: bool
) -> None:
    repo, replacement = _repo(tmp_path / "r"), _repo(tmp_path / "another")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin, checks = GitPlugin(), ChangeChecks()
    ctx = _plugin_context(repo, db)
    kept = plugin.snapshots(ContextRequest(ctx(0)))
    kept.save(plugin.collect(RefreshRequest(ctx(0), Graph())))
    try:
        assert checks.check(Enabled(plugin, ctx(0)), Graph([])) is None
        if replace_checkout:
            repo.rename(tmp_path / "old")
            replacement.rename(repo)
        else:
            (repo / ".git").rename(tmp_path / "old")
            (replacement / ".git").rename(repo / ".git")
        assert checks.check(Enabled(plugin, ctx(1)), Graph([])) is not None

        kept.save(plugin.collect(RefreshRequest(ctx(1), Graph())))
        checks.check(Enabled(plugin, ctx(2)), Graph([]))
        (repo / "src" / "deep" / "a.py").write_text("changed\n", encoding="utf-8")
        assert _until(lambda: checks.check(Enabled(plugin, ctx(3)), Graph([])) is not None)
    finally:
        plugin.stop_watching()


def test_a_worktree_a_new_snapshot_found_is_watched_once_watching_has_started(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin, checks = GitPlugin(), ChangeChecks()
    ctx = _plugin_context(repo, db)
    kept = plugin.snapshots(ContextRequest(ctx(0)))
    kept.save(plugin.collect(RefreshRequest(ctx(0), Graph())))
    try:
        assert checks.check(Enabled(plugin, ctx(0)), Graph([])) is None  # starts watching
        worktree = tmp_path / "wt"
        Git(repo).run("worktree", "add", "-q", "--detach", str(worktree))
        kept.save(plugin.collect(RefreshRequest(ctx(1), Graph())))  # as a refresh stores it
        time.sleep(QUIET_SECONDS)
        checks.check(Enabled(plugin, ctx(2)), Graph([]))  # what adding it moved, taken
        (worktree / "src" / "deep" / "a.py").write_text("changed\n")
        assert _until(lambda: checks.check(Enabled(plugin, ctx(3)), Graph([])) is not None)
    finally:
        plugin.stop_watching()


def test_ignored_files_dont_refresh_the_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin, checks = GitPlugin(), ChangeChecks()
    ctx = _plugin_context(repo, db)
    plugin.snapshots(ContextRequest(ctx(0))).save(plugin.collect(RefreshRequest(ctx(0), Graph())))
    try:
        assert checks.check(Enabled(plugin, ctx(0)), Graph([])) is None  # starts watching
        (repo / "src" / "node_modules" / "dep" / "index.js").write_text("x\n")
        time.sleep(QUIET_SECONDS)
        # git status counts nothing new
        assert checks.check(Enabled(plugin, ctx(1)), Graph([])) is None
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
