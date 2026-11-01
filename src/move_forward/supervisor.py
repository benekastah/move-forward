"""`mfd serve` runs the server in a child process, and replaces it when `reload` sees a
change that passes `mfd check`. A server that fails is started again too.

Ctrl-c is the parent's: the server ignores it, and the parent stops the server as it does
to replace it. The parent itself runs the code it started with, so a change to this module
or `reload` needs `mfd serve` started again by hand.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Protocol

from .reload import PACKAGE_DIR, POLL_SECONDS, passes_check, restart_command, watch

log = logging.getLogger(__name__)

STOP_SECONDS = 10
"""How long a server being replaced has to exit before it's killed."""
QUICK_FAILURE_SECONDS = 30
MAX_QUICK_FAILURES = 3
"""A server failing this many times in a row, each within `QUICK_FAILURE_SECONDS` of
starting, isn't started again."""
SUPERVISED_ENV = "MOVE_FORWARD_SUPERVISED"
"""Set in the server `supervise` runs, so it serves rather than supervising again."""


class Process(Protocol):
    """What `supervise` uses of a `subprocess.Popen`."""

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


def supervised() -> bool:
    """Whether this process is a server `supervise` started."""
    return bool(os.environ.get(SUPERVISED_ENV))


def serve_supervised() -> None:
    """Set up a server `supervise` started: ignore ctrl-c, which the parent answers by
    stopping this server, and on macOS and Linux take that stop (SIGTERM) as ctrl-c, so the
    server winds down as it would. (Windows ends a process outright.)"""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, _interrupt)


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def supervise(
    argv: list[str],
    config_path: Path,
    code: bool,
    popen: Callable[..., Process] = subprocess.Popen,
) -> int:
    """Run `argv`'s `mfd serve` in a child process, in this console, and replace it (less
    the one-shot flags) when `config_path` changes, or with `code` the package's source.

    A server that fails is started again, unless it fails within
    `QUICK_FAILURE_SECONDS` of starting `MAX_QUICK_FAILURES` times in a row (the port taken,
    say): then its exit code is returned, as it is when the server exits cleanly. Ctrl-c
    stops the server and returns 0.
    """
    env = {**os.environ, SUPERVISED_ENV: "1"}
    server = popen([sys.executable, "-m", "move_forward.cli", *argv[1:]], env=env)
    started = time.monotonic()
    quick_failures = 0

    def start_again() -> None:
        nonlocal server, started
        server = popen(restart_command(argv), env=env)
        started = time.monotonic()

    def replace() -> None:
        log.info("restarting the server")
        _stop(server)
        start_again()

    try:
        while True:
            watch(
                replace,
                PACKAGE_DIR if code else None,
                [config_path],
                lambda: passes_check(argv),
                alive=lambda: server.poll() is None,
            )
            exit_code = server.wait()
            if exit_code == 0:
                return 0
            quick = time.monotonic() - started < QUICK_FAILURE_SECONDS
            quick_failures = quick_failures + 1 if quick else 0
            if quick_failures >= MAX_QUICK_FAILURES:
                log.error("the server keeps failing as it starts (exit code %d)", exit_code)
                return exit_code
            log.warning("the server stopped (exit code %d); starting it again", exit_code)
            time.sleep(POLL_SECONDS)
            start_again()
    except KeyboardInterrupt:
        log.info("shutting down on ctrl-c…")
        try:
            _stop(server)
        except KeyboardInterrupt:  # a second ctrl-c: don't wait for it
            server.kill()
        return 0


def _stop(server: Process) -> None:
    """End a server, killing it if it takes over `STOP_SECONDS`. It may be mid-refresh;
    SQLite keeps the database whole either way."""
    server.terminate()
    try:
        server.wait(STOP_SECONDS)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
