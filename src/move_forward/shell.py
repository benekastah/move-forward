"""Running external commands (gh, security, icacls), and stopping them all on shutdown; git
runs through `move_forward.git`."""

import subprocess
import sys
import threading
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 120
MAX_COMMAND_IN_ERROR = 200

_stopping = threading.Event()


class CommandError(RuntimeError):
    """A command exited non-zero; the message holds its stderr, and `stdout` what it printed
    there, for a command that reports in its output how far it got (`--json`)."""

    def __init__(self, message: str, stdout: str = ""):
        super().__init__(message)
        self.stdout = stdout


def stop() -> None:
    """Make every command from now on fail at once, so a refresh in flight winds down fast.

    Called on ctrl-c: the interpreter waits for the refresh's worker threads to finish, and
    a refresh runs dozens of git and gh commands in a row. `move_forward.git.Git` asks
    `stopping` too.
    """
    _stopping.set()


def stopping() -> bool:
    return _stopping.is_set()


def run(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    input: str | None = None,
    encoding: str = "utf-8",
) -> str:
    """Run a command, with `input` on its stdin, and return its stdout, read as `encoding`:
    UTF-8, which gh writes; a Windows tool of its own (`icacls`) writes in
    the system's OEM code page, `"oem"`.

    Raises CommandError if it exits non-zero, or if `stop` was called.
    """
    if _stopping.is_set():
        raise CommandError(f"{args[0]}: not run, shutting down")
    result = subprocess.run(
        args,
        cwd=cwd,
        input=input,
        capture_output=True,
        # As said, not the locale's default. A byte that isn't `encoding` (in a commit
        # message, say) mustn't fail the whole read.
        encoding=encoding,
        errors="replace",
        timeout=timeout,
        # Python's own files aren't inherited anyway (PEP 446). Without closing the rest, macOS
        # starts a program given by its path (and no `cwd`) with posix_spawn rather than fork, a
        # quarter faster. Not on Windows, where a child would inherit the pipes another thread's
        # command is being given as it starts, and could keep that command's output open.
        close_fds=sys.platform == "win32",
        check=False,
    )
    if result.returncode != 0:
        shown = " ".join(args)
        if len(shown) > MAX_COMMAND_IN_ERROR:
            shown = shown[:MAX_COMMAND_IN_ERROR] + "…"
        raise CommandError(f"{shown}: {result.stderr.strip() or result.returncode}", result.stdout)
    return result.stdout
