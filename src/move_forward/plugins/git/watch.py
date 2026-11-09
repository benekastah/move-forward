"""Watching the repos as they change, with watchfiles (Rust's `notify`: inotify on Linux,
FSEvents on macOS, `ReadDirectoryChangesW` on Windows), so `GitPlugin.changed` knows at once
which repos moved and which worktrees were edited, instead of polling.

A repo's refs and config are watched in its common git directory, recursively: FSEvents
reports a new `worktrees/<name>/HEAD` without its parent directories being created. Of
what changes there, only refs, `HEAD`s, `packed-refs` and `config` count (see
`moves_repo`), and each worktree's index (`git add` writes nothing else), which counts as
an edit to that worktree.

A worktree's edits are watched where git tracks files. On Linux, where inotify watches a
directory at a time, that's each directory in the index, on its own: ignored trees
(`node_modules`, `build`, `.venv`, wherever they are) cost no watches, and a new untracked
directory shows in its parent's events. On macOS and Windows, whose watches cover a tree
at once, it's the worktree, recursively.

An edit only says a worktree may have changed: `changed` asks its `git status` whether the
counts the page shows did. Each set of paths is watched by one `RustNotify` read by one
thread, however many paths: one for the git directories, recursively, and on Linux one for
the worktrees' directories. When either can't be watched (inotify's watch limit, say),
what it covers is left to the polling in `changes` and `status_seconds`.
"""

import logging
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from watchfiles._rust_notify import RustNotify

from ...api.shell import CommandError, git

log = logging.getLogger(__name__)

DEBOUNCE_MS = 200
"""How long a burst of changes is gathered before it's handed over."""
STEP_MS = 50
TIMEOUT_MS = 1000
"""How often a watching thread looks up from waiting, to see whether it should stop."""
PER_DIRECTORY = sys.platform == "linux"
"""Whether worktrees are watched a directory at a time (inotify) rather than as a tree."""


def _within(path: Path, root: Path) -> Path | None:
    """`path` relative to `root`, or None when it's outside it."""
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def moves_repo(relative: Path) -> bool:
    """Whether a change at `relative`, a path in a common git directory, moves the repo:
    `HEAD`, `packed-refs`, anything under `refs/`, a linked worktree's `HEAD` (one added
    `--detach`ed moves no ref), or `config` (where branches' upstreams are set, and git-stack
    keeps its stacks). On Linux, a new worktree's directory too: there a new directory is
    watched only once it's seen, and `git worktree add` writes its `HEAD` at once, on a busy
    machine before the watch is there. Not elsewhere, where a whole tree is watched and a
    directory is reported changed whenever something in it is (a linked worktree's index)."""
    parts = relative.parts
    if not parts:
        return False
    if parts[0] == "refs":
        return True
    if parts[0] == "worktrees":
        return (len(parts) <= 2 and PER_DIRECTORY) or (len(parts) == 3 and parts[2] == "HEAD")
    if len(parts) == 1:
        return parts[0] in ("HEAD", "packed-refs", "config")
    return False


def git_dir(worktree: Path) -> Path | None:
    """A worktree's own git directory: `.git` in the main worktree, or for a linked one the
    directory its `.git` file names. None when it isn't a checkout."""
    dot = worktree / ".git"
    if dot.is_dir():
        return dot
    if not dot.is_file():
        return None
    text = dot.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return None
    return (worktree / text.removeprefix("gitdir:").strip()).resolve()


def tracked_dirs(worktree: Path) -> set[Path]:
    """The worktree and every directory in its index, i.e. with a file git tracks or a new
    one staged."""
    try:
        out = git(worktree, "ls-files", "-z")
    except CommandError:
        return {worktree}
    found = {worktree}
    for name in out.split("\0"):
        for parent in PurePosixPath(name).parents:
            if parent.parts:
                found.add(worktree.joinpath(*parent.parts))
    return found


class _Watch:
    """One `RustNotify` over a set of paths, and the thread handing over its changes."""

    def __init__(
        self, paths: frozenset[Path], recursive: bool, on_change: Callable[[list[Path]], None]
    ):
        self.paths = paths
        self.dead = False
        """Its thread stopped on an error: what it watched is polled until the next `sync`
        watches it anew."""
        self._on_change = on_change
        self._stop = threading.Event()
        self._notify = RustNotify(sorted(str(p) for p in paths), False, False, 0, recursive, True)
        self._thread = threading.Thread(target=self._run, name="git-watch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                changes = self._notify.watch(DEBOUNCE_MS, STEP_MS, TIMEOUT_MS, self._stop)
                if isinstance(changes, set):
                    self._on_change([Path(path) for _, path in changes])
        # Whatever stops it (watchfiles raises its own errors), it must say so and be polled.
        except Exception as e:  # noqa: BLE001
            log.warning("watching %d paths stopped, so polling them: %s", len(self.paths), e)
            self.dead = True

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        self._notify.close()


class Watcher:
    """Watches repos and their worktrees, collecting which moved and which were edited
    until `take` hands them over."""

    def __init__(self) -> None:
        self._sync_lock = threading.Lock()
        """One `sync` at a time."""
        self._watches: dict[bool, _Watch] = {}
        """The watch of the paths watched recursively (True), and of those that aren't."""
        self._listed: dict[Path, tuple[tuple[int, int], set[Path]]] = {}
        """Each worktree's tracked directories, with the index's time and size when they
        were listed, to list them again only once it changed."""
        self._lock = threading.Lock()
        self._repos: set[Path] = set()
        """The common git directories whose refs are watched."""
        self._worktrees: dict[Path, Path] = {}
        """The worktrees whose edits are watched, with their common git directories."""
        self._indexes: dict[Path, Path] = {}
        """Each watched worktree, by its index file."""
        self._moved: set[Path] = set()
        self._edited: set[Path] = set()

    def stop(self) -> None:
        with self._sync_lock:
            for watch in self._watches.values():
                watch.close()
            self._watches.clear()

    def _dead(self, recursive: bool) -> bool:
        watch = self._watches.get(recursive)
        return watch is not None and watch.dead

    @property
    def repos(self) -> set[Path]:
        """The repos watched; none while the watch of them is dead, so they're polled."""
        with self._lock:
            return set() if self._dead(True) else set(self._repos)

    @property
    def worktrees(self) -> set[Path]:
        """The worktrees watched; none while the watch of them is dead, so they're polled."""
        with self._lock:
            dead = self._dead(False) if PER_DIRECTORY else self._dead(True)
            return set() if dead else set(self._worktrees)

    def sync(self, worktrees: dict[Path, Path]) -> None:
        """Watch exactly these worktrees (each with its common git directory, all resolved)
        and their repos, following what each tracks."""
        with self._sync_lock:
            commons = frozenset(c for c in worktrees.values() if c.is_dir())
            if PER_DIRECTORY:
                repos_ok = self._ensure(True, commons)
                dirs = frozenset(d for w in worktrees for d in self._tracked(w) if d.is_dir())
                worktrees_ok = self._ensure(False, dirs) and repos_ok
            else:
                trees = commons | {w for w in worktrees if w.is_dir()}
                repos_ok = worktrees_ok = self._ensure(True, trees)
            indexes: dict[Path, Path] = {}
            for worktree in worktrees:
                if (own := git_dir(worktree)) is not None:
                    indexes[(own / "index").resolve()] = worktree
            with self._lock:
                self._repos = set(commons) if repos_ok else set()
                self._worktrees = dict(worktrees) if worktrees_ok else {}
                self._indexes = indexes if worktrees_ok else {}

    def _tracked(self, worktree: Path) -> set[Path]:
        """`tracked_dirs`, listed again only when the worktree's index changed."""
        own = git_dir(worktree)
        try:
            stat = (own / "index").stat() if own else None
        except OSError:
            stat = None
        if stat is None:
            return tracked_dirs(worktree)
        key = (stat.st_mtime_ns, stat.st_size)
        listed = self._listed.get(worktree)
        if listed is None or listed[0] != key:
            listed = (key, tracked_dirs(worktree))
            self._listed[worktree] = listed
        return listed[1]

    def _ensure(self, recursive: bool, paths: frozenset[Path]) -> bool:
        """Watch `paths` (recursively or not), unless they already are; whether they are."""
        current = self._watches.get(recursive)
        if current is not None and current.paths == paths and not current.dead:
            return True
        if current is not None:
            current.close()
            del self._watches[recursive]
        if not paths:
            return True
        try:
            self._watches[recursive] = _Watch(paths, recursive, self._note)
        except (OSError, RuntimeError) as e:  # the watch limit, or a path gone since
            log.warning("can't watch %d paths, so polling them: %s", len(paths), e)
            return False
        return True

    def take(self) -> tuple[set[Path], set[Path]]:
        """The repos (common git directories) that moved and the worktrees edited since
        the last call."""
        with self._lock:
            moved, edited = self._moved, self._edited
            self._moved, self._edited = set(), set()
        return moved, edited

    def _note(self, paths: Iterable[Path]) -> None:
        with self._lock:
            for path in paths:
                self._note_one(path)

    def _note_one(self, path: Path) -> None:
        if (worktree := self._indexes.get(path)) is not None:
            self._edited.add(worktree)
            return
        for common in self._repos:
            if (relative := _within(path, common)) is not None:
                if moves_repo(relative):
                    self._moved.add(common)
                return
        worktree = max(
            (w for w in self._worktrees if _within(path, w) is not None),
            key=lambda w: len(w.parts),
            default=None,
        )
        if worktree is None:
            return
        relative = _within(path, worktree)
        if relative is not None and relative.parts[:1] != (".git",):
            self._edited.add(worktree)
