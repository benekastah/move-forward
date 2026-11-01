"""git telling that a repository moved, from its ref files' times."""

from pathlib import Path

from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.git import changes
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.repositories import KvRepository
from move_forward.shell import git


def _repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "me@example.com")
    git(path, "config", "user.name", "Me")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "commit", "-q", "--allow-empty", "-m", "first")
    return path


def test_commits_checkouts_and_branches_change_the_fingerprint_but_status_does_not(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path / "r")
    seen = [changes.fingerprint([repo])]

    def moved() -> bool:
        seen.append(changes.fingerprint([repo]))
        return seen[-1] != seen[-2]

    (repo / "a.txt").write_text("a")
    git(repo, "status")  # rewrites the index, as editors do
    assert not moved()
    git(repo, "add", "a.txt")
    assert not moved()  # staged, not committed: left to refresh_seconds
    git(repo, "commit", "-q", "-m", "a")
    assert moved()
    git(repo, "branch", "feature")
    assert moved()
    git(repo, "checkout", "-q", "feature")
    assert moved()
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "other")
    assert moved()
    git(tmp_path / "wt", "commit", "-q", "--allow-empty", "-m", "in the worktree")
    assert moved()


def test_a_worktree_is_watched_through_its_main_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "other")
    assert changes.common_dir(tmp_path / "wt") == changes.common_dir(repo)
    assert changes.fingerprint([repo, tmp_path / "wt"]) == changes.fingerprint([repo])
    assert changes.common_dir(tmp_path) is None  # not a checkout


def test_the_plugin_says_changed_once_per_move_after_its_first_look(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    ctx = Context(
        config=GitConfig(repos=[repo]),
        email="me@example.com",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "git"),
    )
    plugin = GitPlugin()
    assert not plugin.changed(ctx, Graph([]))  # nothing to compare against yet
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ctx, Graph([]))
    assert not plugin.changed(ctx, Graph([]))
