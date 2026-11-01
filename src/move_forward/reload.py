"""Restart the server when its config changes, and with `mfd serve --reload` (for
development) when its source changes too.

A thread polls the modification times of the config file, and with `--reload` the
package's files. When one changes, the changed Python files are compiled, then `mfd check` runs in a separate process
to import every module and load the config. Only if both pass does the process replace
itself with the same command; otherwise the error is logged, the running server carries on,
and the next change tries again.
"""

import logging
import os
import py_compile
import subprocess
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
SERVE_COMMAND = "serve"
CHECK_COMMAND = "check"
CHECK_TIMEOUT_SECONDS = 60


def watched_files(root: Path | None = PACKAGE_DIR) -> list[Path]:
    """The source files under `root`; none without one."""
    if root is None:
        return []
    return sorted(p for p in root.rglob("*") if p.suffix in WATCHED_SUFFIXES and p.is_file())


def snapshot(files: list[Path]) -> dict[Path, float]:
    """Each file's modification time; -1 for one that went missing."""
    return {p: p.stat().st_mtime if p.exists() else -1 for p in files}


def changed(
    before: dict[Path, float], root: Path | None = PACKAGE_DIR, extra: list[Path] | None = None
) -> list[Path]:
    """Files added, removed or modified since `before`; `extra` are watched besides `root`'s."""
    now = snapshot(watched_files(root) + (extra or []))
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


def check_command(argv: list[str]) -> list[str]:
    """`mfd check` with the same global options (e.g. `-c <config>`) as `argv`'s `serve`."""
    command = restart_command(argv)
    return command[: command.index(SERVE_COMMAND)] + [CHECK_COMMAND]


def passes_check(argv: list[str] | None = None) -> bool:
    """Whether the code as it is now imports and loads the config, in a fresh process; the
    error is logged when not."""
    try:
        result = subprocess.run(
            check_command(argv or sys.argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        log.error("not reloading: `mfd check` took over %ds", CHECK_TIMEOUT_SECONDS)
        return False
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        log.error("not reloading, the new code fails:\n%s", "\n".join(lines[-15:]))
        return False
    return True


def restart() -> None:
    command = restart_command(sys.argv)
    log.info("restarting: %s", " ".join(command))
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(command[0], command)


def watch(
    restart: Callable[[], None] = restart,
    root: Path | None = PACKAGE_DIR,
    extra: list[Path] | None = None,
    check: Callable[[], bool] = passes_check,
) -> None:
    """Poll until a change compiles and passes `check`, then call `restart` (which normally
    doesn't return). A change that fails is waited out until the next one."""
    extra = extra or []
    before = snapshot(watched_files(root) + extra)
    while True:
        time.sleep(POLL_SECONDS)
        files = changed(before, root, extra)
        if not files:
            continue
        time.sleep(SETTLE_SECONDS)
        files = changed(before, root, extra)
        before = snapshot(watched_files(root) + extra)
        if compiles(files) and check():
            log.info("changed: %s", ", ".join(p.name for p in files))
            restart()


def start(config_path: Path, code: bool) -> None:
    """Watch `config_path`, and with `code` the package's source, in a daemon thread."""
    root = PACKAGE_DIR if code else None
    threading.Thread(
        target=watch, kwargs={"root": root, "extra": [config_path]}, name="reload", daemon=True
    ).start()
