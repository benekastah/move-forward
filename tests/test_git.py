"""Running git as the dashboard does."""

import threading
from pathlib import Path

import pytest
from git_client import NotStartedError
from git_client.testing import real_git

from move_forward import shell
from move_forward.git import GIT_ENV, Git
from move_forward.shell import DEFAULT_TIMEOUT_SECONDS


def test_no_git_starts_once_the_dashboard_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refresh in flight winds down fast: each of its git commands fails at once, a
    yes-or-no one included, rather than say no."""
    monkeypatch.setattr(shell, "_stopping", threading.Event())  # don't stop other tests
    shell.stop()
    git = Git(tmp_path)
    with pytest.raises(NotStartedError):
        git.run("init")
    with pytest.raises(NotStartedError):
        git.ok("init")
    with pytest.raises(NotStartedError):  # a long-lived one too, e.g. a CatFile's
        git.popen("init")
    assert not (tmp_path / ".git").exists()


def test_a_long_lived_git_starts_until_then(tmp_path: Path) -> None:
    with Git(tmp_path).popen("version") as git:
        out, _ = git.communicate()
    assert out.startswith(b"git version ")


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
