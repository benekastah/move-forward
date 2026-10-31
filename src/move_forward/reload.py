"""Restart the server when its source changes: `mfd serve --reload`, for development.

A thread polls the modification times of the package's files. When one changes, the
changed Python files are compiled first (a syntax error is logged and waited out, rather
than killing the server), then the process replaces itself with the same command.
"""

import logging
import os
import py_compile
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).parent
WATCHED_SUFFIXES = {".py", ".css", ".graphql"}
POLL_SECONDS = 1.0
SETTLE_SECONDS = 0.3
"""Editors write several files in a row; wait for the burst to finish before restarting."""
ONE_SHOT_FLAGS = {"--open"}
"""Flags that should act once, not again on every restart."""


def watched_files(root: Path = PACKAGE_DIR) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix in WATCHED_SUFFIXES and p.is_file())


def snapshot(files: list[Path]) -> dict[Path, float]:
    """Each file's modification time; -1 for one that went missing."""
    return {p: p.stat().st_mtime if p.exists() else -1 for p in files}


def changed(before: dict[Path, float], root: Path = PACKAGE_DIR) -> list[Path]:
    """Files added, removed or modified since `before`."""
    now = snapshot(watched_files(root))
    return sorted(p for p in before.keys() | now.keys() if before.get(p, -1) != now.get(p, -1))


def compiles(files: list[Path]) -> bool:
    """Whether every existing Python file in `files` compiles; errors are logged."""
    ok = True
    for path in files:
        if path.suffix == ".py" and path.exists():
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as e:
                log.error("not reloading, %s", e.msg.strip())
                ok = False
    return ok


def restart_command(argv: list[str]) -> list[str]:
    """The command to re-run as: the same arguments, less the one-shot flags."""
    return [sys.executable, "-m", "move_forward.cli"] + [
        arg for arg in argv[1:] if arg not in ONE_SHOT_FLAGS
    ]


def restart() -> None:
    command = restart_command(sys.argv)
    log.info("restarting: %s", " ".join(command))
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(command[0], command)


def watch(restart: Callable[[], None] = restart, root: Path = PACKAGE_DIR) -> None:
    """Poll until a change compiles, then call `restart` (which normally doesn't return)."""
    before = snapshot(watched_files(root))
    while True:
        time.sleep(POLL_SECONDS)
        files = changed(before, root)
        if not files:
            continue
        time.sleep(SETTLE_SECONDS)
        files = changed(before, root)
        before = snapshot(watched_files(root))
        if compiles(files):
            log.info("changed: %s", ", ".join(p.name for p in files))
            restart()


def start() -> None:
    """Watch in a daemon thread."""
    threading.Thread(target=watch, name="reload", daemon=True).start()
