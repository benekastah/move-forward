"""git telling that a repository moved, from its ref files' times, or that its uncommitted
edits changed, from `git status`."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW

from move_forward.change_checks import ChangeChecks
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import (
    CollectRequest,
    Context,
    EntitiesRequest,
    LoadRequest,
    StoreRequest,
)
from move_forward.plugins.git import changes, client
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalRepo
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git.repository import MIGRATIONS
from move_forward.registry import Enabled
from move_forward.repositories import ChangeRepository, KvRepository, ProblemRepository
from move_forward.shell import CommandError, git, git_common_dir


class _Server:
    """What the server does with the plugin's change checks: asks them, keeping what they said
    (`ChangeChecks`), and refreshes the plugin with what moved."""

    def __init__(self, plugin: GitPlugin) -> None:
        self.plugin = plugin
        self.checks = ChangeChecks()

    def changed(self, ctx: Context[GitConfig]) -> bool:
        """Whether a check now would have the server refresh the plugin."""
        return self.checks.check(Enabled(self.plugin, ctx), Graph([])) is not None

    def refresh(self, ctx: Context[GitConfig]) -> None:
        """Collect what moved, or everything, and store it, as a refresh does."""
        e = Enabled(self.plugin, ctx)
        taken = self.checks.take(e, Graph([]))
        found = self.plugin.collect(CollectRequest(ctx, taken.full, taken.subjects))
        self.plugin.store(StoreRequest(ctx, found))
        self.checks.stored(e, taken)


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
    git(repo, "config", "branch.feature.stackParent", "main")  # as git stack tracks it
    assert moved()


def test_a_worktree_is_watched_through_its_main_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "other")
    assert changes.fingerprint([repo, tmp_path / "wt"]) == changes.fingerprint([repo])


def test_the_plugin_says_changed_after_a_move_and_keeps_it_until_a_refresh_reads_it(
    tmp_path: Path,
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
    server = _Server(GitPlugin(watching=False))
    assert not server.changed(ctx)  # nothing to compare against yet
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    assert server.changed(ctx)
    # Kept until a refresh reads it, but asking for one only once: nothing new moved since.
    assert not server.changed(ctx)
    assert ChangeRepository(db).get("git").subjects == {str(git_common_dir(repo))}
    server.refresh(ctx)
    assert not server.changed(ctx)
    assert ChangeRepository(db).get("git").subjects == set()


def test_after_a_change_only_the_repos_that_moved_are_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    server = _Server(GitPlugin(watching=False))

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
        server.refresh(ctx(minutes))

    assert not server.changed(ctx(0))  # the first look
    refresh(0)
    assert sorted(read) == ["a", "b"]
    git(b, "commit", "-q", "--allow-empty", "-m", "second")
    assert server.changed(ctx(1))
    read.clear()
    refresh(1)
    assert read == ["b"]  # a is kept as stored
    stored = server.plugin.load(LoadRequest(ctx(1)))
    assert stored is not None and [r.dir for r in stored.repos] == [str(a), str(b)]
    git(a, "commit", "-q", "--allow-empty", "-m", "second")
    assert server.changed(ctx(11))
    read.clear()
    refresh(11)  # ten minutes since every repo was read: all of them, whatever moved
    assert sorted(read) == ["a", "b"]


def test_uncommitted_edits_are_seen_every_status_seconds(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin(watching=False)
    server = _Server(plugin)

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
    assert not server.changed(ctx(0))  # as stored
    (repo / "new.txt").write_text("x\n")
    assert not server.changed(ctx(10))  # status_seconds not up yet
    assert not server.changed(ctx(40, status_seconds=0))  # off
    assert server.changed(ctx(40))
    assert ChangeRepository(db).get("git").subjects == {str(git_common_dir(repo))}
    server.refresh(ctx(40))  # reads it, untracked file and all
    assert not server.changed(ctx(80))


def test_edits_after_a_collect_that_failed_ask_again_only_when_they_move_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin(watching=False)
    server = _Server(plugin)

    def ctx(seconds: int) -> Context[GitConfig]:
        return Context(
            config=GitConfig(repos=[repo], status_seconds=30),
            email="me@example.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(seconds=seconds),
            db=db,
            kv=KvRepository(db, "git"),
        )

    plugin.store(StoreRequest(ctx(0), plugin.collect(CollectRequest(ctx(0)))))
    assert not server.changed(ctx(0))
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    assert server.changed(ctx(40))

    def failing(*args: Any, **kwargs: Any) -> LocalRepo:
        raise CommandError("git: index.lock exists")

    monkeypatch.setattr(client, "collect_repo", failing)
    with pytest.raises(CommandError):
        server.refresh(ctx(40))
    assert not server.changed(ctx(80))  # as the last look saw it, though not as stored
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    assert server.changed(ctx(120))
    (repo / "new.txt").unlink()
    (repo / "other.txt").unlink()
    assert not server.changed(ctx(160))  # back as stored: nothing to read again
    assert ChangeRepository(db).get("git").subjects == {str(git_common_dir(repo))}


def test_edits_back_as_the_last_look_saw_them_after_a_newer_store_are_still_seen(
    tmp_path: Path,
) -> None:
    """The last look is forgotten once a collect stores, so what's stored is what's compared."""
    repo = _repo(tmp_path / "r")
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    plugin = GitPlugin(watching=False)
    server = _Server(plugin)

    def ctx(seconds: int) -> Context[GitConfig]:
        return Context(
            config=GitConfig(repos=[repo], status_seconds=30),
            email="me@example.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(seconds=seconds),
            db=db,
            kv=KvRepository(db, "git"),
        )

    server.refresh(ctx(0))
    assert not server.changed(ctx(0))
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    assert server.changed(ctx(40))  # looked at: one untracked file
    git(repo, "add", "new.txt")
    git(repo, "commit", "-q", "-m", "new")
    server.refresh(ctx(40))  # stores none untracked, with no look in between
    (repo / "other.txt").write_text("x\n", encoding="utf-8")
    assert server.changed(ctx(80))  # one untracked again, as the last look saw, but not stored


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
    server = _Server(GitPlugin(watching=False))
    assert not server.changed(ctx)
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    assert server.changed(ctx)

    def failing(*args: Any, **kwargs: Any) -> LocalRepo:
        raise CommandError("git: index.lock exists")

    monkeypatch.setattr(client, "collect_repo", failing)
    with pytest.raises(CommandError):
        server.refresh(ctx)
    assert ChangeRepository(db).get("git").full_at is None  # the next collect reads every repo
    assert ChangeRepository(db).get("git").subjects == {str(git_common_dir(repo))}  # kept
    assert not server.changed(ctx)  # no refresh every check


@pytest.mark.parametrize("second", ["Acme/widgets", "acme/WIDGETS"])
def test_two_clones_of_one_github_repo_are_a_problem(
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
    [problem] = ProblemRepository(db).of([""])[""]
    assert problem.startswith(f"{clones[0]}, {clones[1]} are each a clone of Acme/widgets")
    # Warned of once, not at every collect, nor again after a restart.
    plugin.collect(CollectRequest(ctx))
    GitPlugin(watching=False).collect(CollectRequest(ctx))
    [warning] = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warning == problem
    plugin.collect(CollectRequest(replace(ctx, config=GitConfig(repos=clones[:1]))))
    assert ProblemRepository(db).of([""]) == {}  # one clone left: nothing wrong
