"""Running external commands (git aside: see test_git.py)."""

import subprocess
import sys
from typing import Any

import pytest
from conftest import Outside

from move_forward import shell
from move_forward.shell import CommandError, run


def test_a_failing_command_keeps_what_it_printed(outside: Outside) -> None:
    outside.allow(sys.executable)  # the child, a script of our own, isn't guarded
    script = "import sys; print('so far'); print('broke', file=sys.stderr); sys.exit(3)"
    with pytest.raises(CommandError, match="broke") as failed:
        run([sys.executable, "-c", script])
    assert failed.value.stdout.strip() == "so far"


def test_only_windows_closes_what_a_command_would_inherit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elsewhere Python's own descriptors aren't inherited, and not closing them lets macOS
    start a program faster. On Windows a command would inherit the pipes another thread is
    setting up, and could hold that thread's command's output open: a hang CI rarely sees."""
    closed: list[bool] = []

    def started(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        closed.append(kwargs["close_fds"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(shell.subprocess, "run", started)
    for platform in ("win32", "darwin", "linux"):
        monkeypatch.setattr(shell.sys, "platform", platform)
        run(["git", "--version"])
    assert closed == [True, False, False]
