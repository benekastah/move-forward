"""Running git as the dashboard does, and finding a checkout's repository."""

import threading
from pathlib import Path

import pytest
from git_client.testing import real_git

from move_forward import shell
from move_forward.git import GIT_ENV, Git, NotRunError, git_common_dir
from move_forward.shell import DEFAULT_TIMEOUT_SECONDS


def test_no_git_starts_once_the_dashboard_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refresh in flight winds down fast: each of its git commands fails at once, a
    yes-or-no one included, rather than say no."""
    monkeypatch.setattr(shell, "_stopping", threading.Event())  # don't stop other tests
    shell.stop()
    git = Git(tmp_path)
    with pytest.raises(NotRunError, match="^git: not run, shutting down$"):
        git.run("init")
    with pytest.raises(NotRunError):
        git.ok("init")
    with pytest.raises(NotRunError):  # a long-lived one too, e.g. a CatFile's
        git.popen("init")
    assert not (tmp_path / ".git").exists()


def test_a_command_is_stopped_after_two_minutes_unless_given_its_own_timeout(
    tmp_path: Path,
) -> None:
    assert Git(tmp_path).timeout == DEFAULT_TIMEOUT_SECONDS == 120
    assert Git(tmp_path, timeout=600).timeout == 600


@pytest.mark.usefixtures("fresh_find_git")
def test_git_is_the_one_its_variable_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    named = str(tmp_path / "elsewhere" / "git")
    monkeypatch.setenv(GIT_ENV, named)
    assert Git(tmp_path).program == named


def test_the_tests_run_gits_own_program(tmp_path: Path) -> None:
    assert Git(tmp_path).program == real_git()


def test_output_is_what_git_printed_with_what_isnt_utf8_replaced(tmp_path: Path) -> None:
    git = Git(tmp_path)
    git.run("init", "-q")
    blob = git.run("hash-object", "-w", "--stdin", input=" caf\udce9\n")
    assert git.output("cat-file", "blob", blob) == " caf�\n"


def _checkout(path: Path) -> Path:
    path.mkdir()
    git = Git(path)
    git.run("init", "-q")
    git.run("commit", "-q", "--allow-empty", "-m", "a")
    return path


def test_a_main_checkouts_git_directory_is_its_dot_git(tmp_path: Path) -> None:
    repo = _checkout(tmp_path / "repo")
    assert git_common_dir(repo) == repo / ".git"


def test_a_linked_worktrees_git_directory_is_its_main_repositorys(tmp_path: Path) -> None:
    repo = _checkout(tmp_path / "repo")
    Git(repo).run("worktree", "add", "-q", "-b", "side", str(tmp_path / "linked"))
    assert git_common_dir(tmp_path / "linked") == (repo / ".git").resolve()


def test_what_isnt_a_checkout_has_no_git_directory(tmp_path: Path) -> None:
    assert git_common_dir(tmp_path) is None
    (tmp_path / ".git").write_text("not a pointer\n", encoding="utf-8")
    assert git_common_dir(tmp_path) is None
