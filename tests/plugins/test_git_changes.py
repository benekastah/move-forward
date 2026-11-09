"""git telling that a repository moved, from its ref files' times, or that its uncommitted
edits changed, from `git status`."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import (
    ChangedRequest,
    CollectRequest,
    Context,
    EntitiesRequest,
    LoadRequest,
    StoreRequest,
)
from move_forward.plugins.git import changes, client
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalRepo
from move_forward.plugins.git.plugin import LAST_FULL, MOVED, GitPlugin
from move_forward.plugins.git.repository import MIGRATIONS
from move_forward.repositories import KvRepository
from move_forward.shell import CommandError, git


def _repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "me@example.com")
    git(path, "config", "user.name", "Me")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "commit", "-q", "--allow-empty", "-m", "first")
    return path


def test_commits_checkouts_branches_and_config_change_the_fingerprint_but_status_does_not(
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
    git(repo, "config", "branch.feature.stackParent", "main")  # as git-stack tracks it
    assert moved()


def test_a_worktree_is_watched_through_its_main_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "other")
    assert changes.common_dir(tmp_path / "wt") == changes.common_dir(repo)
    assert changes.fingerprint([repo, tmp_path / "wt"]) == changes.fingerprint([repo])
    assert changes.common_dir(tmp_path) is None  # not a checkout


def test_the_plugin_says_changed_after_a_move_until_a_refresh_reads_it(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    ctx = Context(
        config=GitConfig(repos=[repo]),
        email="me@example.com",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "git"),
    )
    plugin = GitPlugin(watching=False)
    assert not plugin.changed(ChangedRequest(ctx, Graph([])))  # nothing to compare against yet
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ChangedRequest(ctx, Graph([])))
    # Still, until a refresh reads it: core ignores the check right after a refresh, and a
    # move seen there mustn't wait for the next one.
    assert plugin.changed(ChangedRequest(ctx, Graph([])))
    plugin.store(StoreRequest(ctx, plugin.collect(CollectRequest(ctx))))
    assert not plugin.changed(ChangedRequest(ctx, Graph([])))


def test_after_a_change_only_the_repos_that_moved_are_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin(watching=False)

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
        plugin.store(StoreRequest(ctx(minutes), plugin.collect(CollectRequest(ctx(minutes)))))

    assert not plugin.changed(ChangedRequest(ctx(0), Graph([])))  # the first look
    refresh(0)
    assert sorted(read) == ["a", "b"]
    git(b, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ChangedRequest(ctx(1), Graph([])))
    read.clear()
    refresh(1)
    assert read == ["b"]  # a is kept as stored
    stored = plugin.load(LoadRequest(ctx(1)))
    assert stored is not None and [r.dir for r in stored.repos] == [str(a), str(b)]
    git(a, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ChangedRequest(ctx(11), Graph([])))
    read.clear()
    refresh(11)  # ten minutes since every repo was read: all of them, whatever moved
    assert sorted(read) == ["a", "b"]


def test_uncommitted_edits_are_seen_every_status_seconds(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin(watching=False)

    def ctx(seconds: int, status_seconds: int = 30) -> Context[GitConfig]:
        return Context(
            config=GitConfig(repos=[repo], status_seconds=status_seconds),
            email="me@example.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(seconds=seconds),
            db=db,
            kv=KvRepository(db, "git"),
        )

    plugin.store(StoreRequest(ctx(0), plugin.collect(CollectRequest(ctx(0)))))
    assert not plugin.changed(ChangedRequest(ctx(0), Graph([])))  # as stored
    (repo / "new.txt").write_text("x\n")
    assert not plugin.changed(ChangedRequest(ctx(10), Graph([])))  # status_seconds not up yet
    assert not plugin.changed(ChangedRequest(ctx(40, status_seconds=0), Graph([])))  # off
    assert plugin.changed(ChangedRequest(ctx(40), Graph([])))
    assert KvRepository(db, "git").get(MOVED) == json.dumps([str(changes.common_dir(repo))])
    plugin.store(
        StoreRequest(ctx(40), plugin.collect(CollectRequest(ctx(40))))
    )  # reads it, untracked file and all
    assert not plugin.changed(ChangedRequest(ctx(80), Graph([])))


def test_a_collect_that_fails_isnt_counted_as_full_and_doesnt_ask_again_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    ctx = Context(
        config=GitConfig(repos=[repo]),
        email="me@example.com",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "git"),
    )
    plugin = GitPlugin(watching=False)
    assert not plugin.changed(ChangedRequest(ctx, Graph([])))
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    assert plugin.changed(ChangedRequest(ctx, Graph([])))

    def failing(*args: Any, **kwargs: Any) -> LocalRepo:
        raise CommandError("git: index.lock exists")

    monkeypatch.setattr(client, "collect_repo", failing)
    with pytest.raises(CommandError):
        plugin.collect(CollectRequest(ctx))
    assert ctx.kv.get(LAST_FULL) is None  # the next collect reads every repo
    assert not plugin.changed(ChangedRequest(ctx, Graph([])))  # no refresh every check


@pytest.mark.parametrize("second", ["Acme/widgets", "acme/WIDGETS"])
def test_two_clones_of_one_github_repo_are_warned_of(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, second: str
) -> None:
    """Published once, by the first's name, and warned of: GitHub's names ignore case, so remotes
    that differ in it name the same repo."""
    clones = [_repo(tmp_path / "a"), _repo(tmp_path / "b")]
    for clone, name in zip(clones, ["Acme/widgets", second], strict=True):
        git(clone, "remote", "add", "origin", f"git@github.com:{name}.git")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    ctx = Context(
        config=GitConfig(repos=clones),
        email="me@example.com",
        teams=[],
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "git"),
    )
    plugin = GitPlugin(watching=False)
    found = plugin.collect(CollectRequest(ctx))
    repos = [e for e in plugin.entities(EntitiesRequest(ctx, found)) if e.ref.kind == "git.repo"]
    assert [(e.ref.id, e.attrs["dir"]) for e in repos] == [("Acme/widgets", str(clones[0]))]
    branches = [
        e for e in plugin.entities(EntitiesRequest(ctx, found)) if e.ref.kind == "git.branch"
    ]
    assert {r.id for b in branches for r in b.refs} == {"Acme/widgets"}  # the one published
    plugin.collect(CollectRequest(ctx))  # warned of once a run, not every collect
    [warning] = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert f"{clones[0]}, {clones[1]} are each a clone of Acme/widgets" in warning
