"""Running external commands (git, gh, security)."""

import subprocess
import threading
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 120
MAX_COMMAND_IN_ERROR = 200

_stopping = threading.Event()


class CommandError(RuntimeError):
    """A command exited non-zero; the message holds its stderr."""


def stop() -> None:
    """Make every command from now on fail at once, so a refresh in flight winds down fast.

    Called on ctrl-c: the interpreter waits for the refresh's worker threads to finish, and
    a refresh runs dozens of git and gh commands in a row.
    """
    _stopping.set()


def stopping() -> bool:
    return _stopping.is_set()


def run(args: list[str], cwd: Path | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Run a command and return its stdout.

    Raises CommandError if it exits non-zero, or if `stop` was called.
    """
    if _stopping.is_set():
        raise CommandError(f"{args[0]}: not run, shutting down")
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        shown = " ".join(args)
        if len(shown) > MAX_COMMAND_IN_ERROR:
            shown = shown[:MAX_COMMAND_IN_ERROR] + "…"
        raise CommandError(f"{shown}: {result.stderr.strip() or result.returncode}")
    return result.stdout


def git(repo: Path, *args: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Run `git -C <repo> <args>` and return its stdout."""
    return run(["git", "-C", str(repo), *args], timeout=timeout)
