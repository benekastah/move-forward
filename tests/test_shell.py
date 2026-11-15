"""Running external commands."""

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import REAL_GIT, Outside

from move_forward.shell import GIT_ENV, CommandError, git, git_common_dir, git_program, run


def test_a_failing_command_keeps_what_it_printed(outside: Outside) -> None:
    outside.allow(sys.executable)  # the child, a script of our own, isn't guarded
    script = "import sys; print('so far'); print('broke', file=sys.stderr); sys.exit(3)"
    with pytest.raises(CommandError, match="broke") as failed:
        run([sys.executable, "-c", script])
    assert failed.value.stdout.strip() == "so far"


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
    assert git_program() == str(REAL_GIT) and REAL_GIT.is_file()


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
