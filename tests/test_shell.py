"""Running external commands."""

import sys
from pathlib import Path

import pytest

from move_forward.shell import CommandError, git, git_common_dir, run


def test_a_failing_command_keeps_what_it_printed() -> None:
    script = "import sys; print('so far'); print('broke', file=sys.stderr); sys.exit(3)"
    with pytest.raises(CommandError, match="broke") as failed:
        run([sys.executable, "-c", script])
    assert failed.value.stdout.strip() == "so far"


def _checkout(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(
        path,
        "-c",
        "user.email=me@x",
        "-c",
        "user.name=Me",
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
