"""Running external commands."""

import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Outside
from git_client.testing import real_git

from move_forward import shell
from move_forward.shell import GIT_ENV, CommandError, git, git_common_dir, git_program, run


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


@pytest.fixture
def looked_up_again() -> Iterator[None]:
    """`git_program` forgets what it found, before the test and after it."""
    git_program.cache_clear()
    yield
    git_program.cache_clear()


@pytest.mark.usefixtures("looked_up_again")
def test_git_is_the_one_its_variable_names_else_the_one_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(GIT_ENV, str(tmp_path / "nowhere" / "git"))
    with pytest.raises(FileNotFoundError):
        git(tmp_path, "status")
    git_program.cache_clear()
    monkeypatch.delenv(GIT_ENV)
    assert git_program() == shutil.which("git") and Path(git_program()).is_absolute()
    git_program.cache_clear()
    monkeypatch.setenv("PATH", str(tmp_path))
    assert git_program() == "git"  # for the error saying it isn't there


def test_the_tests_run_gits_own_program() -> None:
    assert git_program() == real_git()


def _checkout(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(
        path,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "a",
    )
    return path


def test_a_main_checkouts_git_directory_is_its_dot_git(tmp_path: Path) -> None:
    repo = _checkout(tmp_path / "repo")
    assert git_common_dir(repo) == repo / ".git"


def test_a_linked_worktrees_git_directory_is_its_main_repositorys(tmp_path: Path) -> None:
    repo = _checkout(tmp_path / "repo")
    git(repo, "worktree", "add", "-q", "-b", "side", str(tmp_path / "linked"))
    assert git_common_dir(tmp_path / "linked") == (repo / ".git").resolve()


def test_what_isnt_a_checkout_has_no_git_directory(tmp_path: Path) -> None:
    assert git_common_dir(tmp_path) is None
    (tmp_path / ".git").write_text("not a pointer\n", encoding="utf-8")
    assert git_common_dir(tmp_path) is None
