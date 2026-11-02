"""Watching the repos as they change, with watchdog (inotify on Linux, FSEvents on macOS,
`ReadDirectoryChangesW` on Windows), so `GitPlugin.changed` knows at once which repos
moved and which worktrees were edited, instead of polling.

A repo's refs and config are watched in its common git directory, recursively: one
watch, and FSEvents reports a new `worktrees/<name>/HEAD` without its parent directories
being created. Of what changes there, only refs, `HEAD`s, `packed-refs` and `config` count
(see `moves_repo`); objects, logs and the index don't.

A worktree's edits are watched in its root (not recursively) and in each top-level
directory git tracks (recursively), so the big ignored trees usually at the top
(`node_modules`, `build`, `.venv`) cost no watches; a new untracked directory shows in the
root's events. watchdog runs a thread per watched path, which is why it's these few and not
every directory.

An edit only says a worktree may have changed: `changed` asks its `git status` whether the
counts the page shows did. A repo or worktree with a path that can't be watched
(inotify's watch limit, say) is left to the polling in `changes` and `status_seconds`, all
of it: half-watched would miss changes.
"""

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

from ...shell import CommandError, git

log = logging.getLogger(__name__)

_IGNORED_EVENTS = {"opened", "closed_no_write"}
"""Reads, not changes: git's own `git status` opens files."""


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
    keeps its stacks)."""
    parts = relative.parts
    if not parts:
        return False
    if parts[0] == "refs":
        return True
    if len(parts) == 1:
        return parts[0] in ("HEAD", "packed-refs", "config")
    return parts[0] == "worktrees" and len(parts) == 3 and parts[2] == "HEAD"


def tracked_top_dirs(worktree: Path) -> list[Path]:
    """The top-level directories git tracks in `worktree`, as of its `HEAD`; none in a
    repo without commits."""
    try:
        out = git(worktree, "ls-tree", "-d", "--name-only", "-z", "HEAD")
    except CommandError:
        return []
    return [worktree / name for name in out.split("\0") if name]


class Watcher(FileSystemEventHandler):
    """Watches repos and their worktrees, collecting which moved and which were edited
    until `take` hands them over.

    watchdog calls `on_any_event` holding its own lock, so the observer is only scheduled
    and unscheduled outside `_lock`, which guards what the events are matched against."""

    def __init__(self, observer: BaseObserver | None = None):
        self._observer = observer or Observer()
        self._observer.start()
        self._sync_lock = threading.Lock()
        """One `sync` at a time."""
        self._places: dict[tuple[Path, bool], ObservedWatch] = {}
        """Each watched path, and whether recursively; only `sync` touches it."""
        self._lock = threading.Lock()
        self._repos: set[Path] = set()
        """The common git directories whose refs are all watched."""
        self._worktrees: dict[Path, Path] = {}
        """The worktrees whose edits are all watched, with their common git directories."""
        self._moved: set[Path] = set()
        self._edited: set[Path] = set()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    @property
    def repos(self) -> set[Path]:
        with self._lock:
            return set(self._repos)

    @property
    def worktrees(self) -> set[Path]:
        with self._lock:
            return set(self._worktrees)

    def sync(self, worktrees: dict[Path, Path]) -> None:
        """Watch exactly these worktrees (each with its common git directory, all resolved)
        and their repos: what's new is added, what's gone dropped, and a worktree whose
        tracked top-level directories changed (a checkout, a commit) follows them."""
        with self._sync_lock:
            wanted: dict[Path, list[tuple[Path, bool]]] = {}
            for common in set(worktrees.values()):
                wanted[common] = [(common, True)]
            for worktree in worktrees:
                wanted[worktree] = [(worktree, False)] + [
                    (d, True) for d in tracked_top_dirs(worktree)
                ]
            places = {place for found in wanted.values() for place in found if place[0].is_dir()}
            for place in set(self._places) - places:
                self._observer.unschedule(self._places.pop(place))
            for place in places - set(self._places):
                if watch := self._schedule(*place):
                    self._places[place] = watch
            watched = {
                key
                for key, found in wanted.items()
                if all(place in self._places for place in found if place[0].is_dir())
            }
            with self._lock:
                self._repos = {c for c in set(worktrees.values()) if c in watched}
                self._worktrees = {w: c for w, c in worktrees.items() if w in watched}

    def take(self) -> tuple[set[Path], set[Path]]:
        """The repos (common git directories) that moved and the worktrees edited since
        the last call."""
        with self._lock:
            moved, edited = self._moved, self._edited
            self._moved, self._edited = set(), set()
        return moved, edited

    def _schedule(self, path: Path, recursive: bool) -> ObservedWatch | None:
        try:
            return self._observer.schedule(self, str(path), recursive=recursive)
        except OSError as e:  # the watch limit, or a path that's gone
            log.warning("can't watch %s, so polling it: %s", path, e)
            return None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in _IGNORED_EVENTS:
            return
        paths = [event.src_path, event.dest_path] if event.dest_path else [event.src_path]
        for raw in paths:
            self._note(Path(raw if isinstance(raw, str) else raw.decode()))

    def _note(self, path: Path) -> None:
        with self._lock:
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
