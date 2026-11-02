"""git telling that a repository moved, from its ref files' times."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.git import changes, client
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalRepo
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git.repository import MIGRATIONS
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


def test_after_a_change_only_the_repos_that_moved_are_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin()

    def ctx(minutes: int) -> Context[GitConfig]:
        return Context(
            config=GitConfig(repos=[a, b], refresh_seconds=600),
            email="me@example.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(minutes=minutes),
            db=db,
            kv=KvRepository(db, "git"),
        )

    read: list[str] = []
    real = client.collect_repo

    def counting(repo: Path, *args: Any) -> LocalRepo:
        read.append(repo.name)
        return real(repo, *args)

    monkeypatch.setattr(client, "collect_repo", counting)

    def refresh(minutes: int) -> None:
        plugin.store(ctx(minutes), plugin.collect(ctx(minutes)))

    assert not plugin.changed(ctx(0), Graph([]))  # the first look
    refresh(0)
    assert sorted(read) == ["a", "b"]
    git(b, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ctx(1), Graph([]))
    read.clear()
    refresh(1)
    assert read == ["b"]  # a is kept as stored
    stored = plugin.load(ctx(1))
    assert stored is not None and [r.dir for r in stored.repos] == [str(a), str(b)]
    git(a, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ctx(11), Graph([]))
    read.clear()
    refresh(11)  # ten minutes since every repo was read: all of them, whatever moved
    assert sorted(read) == ["a", "b"]
