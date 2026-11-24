import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from git_client import Arg, GitError, GitTimeoutError
from git_client.testing import GitCall, RepoTemplates

from move_forward.api.git import git_common_dir
from move_forward.db import Database
from move_forward.git import Git
from move_forward.plugins.git import client as local
from move_forward.plugins.git.models import LocalRepo, LocalSnapshot
from move_forward.plugins.git.repository import MIGRATIONS, LocalRepository

WORKTREES = """\
worktree /repo
HEAD aaaa
branch refs/heads/main

worktree /repo-wt
HEAD bbbb
detached

worktree /repo/.claude/worktrees/agent-1
HEAD cccc
branch refs/heads/agent-1
"""


def test_parse_worktrees() -> None:
    assert local.parse_worktrees(WORKTREES) == [
        ("/repo", "main"),
        ("/repo-wt", None),
        ("/repo/.claude/worktrees/agent-1", "agent-1"),
    ]


def test_parse_status_counts_modified_and_untracked() -> None:
    assert local.parse_status(" M a.py\n?? b.py\nA  c.py\n?? d/\n") == (2, 2)
    assert local.parse_status("") == (0, 0)


def test_parse_branches_tracks_ahead_behind_gone() -> None:
    out = (
        "main\torigin/main\t\t2026-10-30T10:00:00+00:00\t" + "a" * 40 + "\n"
        "feat\torigin/feat\t[ahead 2, behind 1]\t2026-10-29T10:00:00+02:00\n"
        "old\torigin/old\t[gone]\t2026-09-01T10:00:00+00:00\n"
        "local-only\t\t\t2026-10-28T10:00:00+00:00\n"
    )
    branches = {b.name: b for b in local.parse_branches(out)}
    assert branches["main"].upstream == "origin/main" and branches["main"].ahead == 0
    assert branches["main"].sha == "a" * 40 and branches["feat"].sha is None
    assert (branches["feat"].ahead, branches["feat"].behind) == (2, 1)
    assert branches["old"].gone is True
    assert branches["local-only"].upstream is None
    assert branches["feat"].committed_at == datetime(2026, 10, 29, 8, tzinfo=UTC)


def test_github_repo_from_remote_urls() -> None:
    assert local.github_repo("git@github.com:acme/widgets.git") == "acme/widgets"
    assert local.github_repo("https://github.com/acme/widgets") == "acme/widgets"
    assert local.github_repo("git@github-work:acme/widgets.git") is None
    assert local.github_repo("https://gitlab.com/acme/widgets.git") is None
    work = ["github.com", "github-work", "GitHub.Acme.com"]  # an alias, and an Enterprise host
    assert local.github_repo("git@github-work:acme/widgets.git", work) == "acme/widgets"
    assert local.github_repo("ssh://git@github.acme.com:22/acme/tools", work) == "acme/tools"
    assert local.github_repo("https://github.acme.com/acme/tools/", work) == "acme/tools"
    assert local.github_repo("github-work:acme/x", work) == "acme/x"  # scp-like, no user
    assert local.github_repo("../elsewhere", work) is None
    assert local.github_repo("/srv/git/widgets.git", work) is None


def _init_repo(path: Path) -> None:
    path.mkdir()
    Git(path).run("init", "-q", "-b", "main")


def _commit(path: Path, name: str) -> None:
    (path / name).parent.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(name)
    Git(path).run("add", name)
    Git(path).run("commit", "-q", "-m", name)


def _origin_and_clone(root: Path) -> None:
    """`origin`, with a commit of `src/a.txt` on main, and `repo`, a clone of it."""
    origin = root / "origin"
    _init_repo(origin)
    _commit(origin, "src/a.txt")
    Git(root).run("clone", "-q", str(origin), str(root / "repo"))


def _clone(repo_templates: RepoTemplates, tmp_path: Path) -> Path:
    """A clone, `repo`, of `origin` beside it, with one commit: built once for the run."""
    repo_templates.build("origin-and-clone", _origin_and_clone)
    return repo_templates.copy("origin-and-clone", tmp_path / "repos") / "repo"


def test_worktree_status_counts_files_inside_untracked_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    Git(repo).run("add", ".gitignore")
    Git(repo).run("commit", "-q", "-m", "ignore generated files")
    (repo / "new" / "nested").mkdir(parents=True)
    (repo / "new" / "one.txt").write_text("one", encoding="utf-8")
    (repo / "new" / "nested" / "two.txt").write_text("two", encoding="utf-8")
    (repo / "third.txt").write_text("three", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "file.txt").write_text("ignored", encoding="utf-8")

    assert local.worktree_status(repo) == (0, 3)


def test_collect_repo_against_a_real_repo(tmp_path: Path, repo_templates: RepoTemplates) -> None:
    repo = _clone(repo_templates, tmp_path)
    Git(repo).run("switch", "-q", "-c", "feature")
    _commit(repo, "b.txt")
    (repo / "dirty.txt").write_text("x")

    now = datetime.now(UTC) + timedelta(seconds=1)
    result = local.collect_repo(repo, now, 30, ["*/ignored/*"])
    assert result.default_branch == "main"
    assert result.github_repo is None
    assert [(w.branch, w.modified, w.untracked) for w in result.worktrees] == [("feature", 0, 1)]
    feature = next(b for b in result.branches if b.name == "feature")
    assert feature.upstream is None and feature.ahead_of_default == 1
    assert feature.sha == Git(repo).run("rev-parse", "feature").strip()
    assert result.my_dirs == ["src"]


def test_a_listed_worktree_of_another_listed_repo_is_folded_into_it(tmp_path: Path) -> None:
    repo, other = tmp_path / "repo", tmp_path / "other"
    _init_repo(repo)
    _commit(repo, "a.txt")
    _init_repo(other)
    _commit(other, "a.txt")
    second = tmp_path / "repo2"
    Git(repo).run("worktree", "add", "-q", "-b", "side", str(second))
    now = datetime.now(UTC)
    with_main = local.collect([second, other, repo], now, 30, [])
    without_main = local.collect([second, other], now, 30, [])
    assert [Path(r.dir) for r in with_main.repos] == [repo, other]
    assert [Path(r.dir) for r in without_main.repos] == [second, other]


def test_a_main_checkout_with_its_git_directory_elsewhere_is_kept_over_its_worktree(
    tmp_path: Path,
) -> None:
    """Its `.git` is a file, as a linked worktree's is, but names the repository itself."""
    repo = tmp_path / "repo"
    repo_git = tmp_path / "repo.git"
    repo.mkdir()
    Git(repo).run("init", "-q", "-b", "main", f"--separate-git-dir={repo_git}")
    _commit(repo, "a.txt")
    second = tmp_path / "repo2"
    Git(repo).run("worktree", "add", "-q", "-b", "side", str(second))
    # Some Git versions list the separate Git directory as a worktree.
    found = local.collect([second, repo], datetime.now(UTC), 30, [repo_git.as_posix()])
    assert [Path(r.dir) for r in found.repos] == [repo]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows' names can't end in a space")
def test_a_main_checkout_is_found_whatever_its_git_directorys_name(tmp_path: Path) -> None:
    """Git directory names can have trailing spaces and non-UTF-8 bytes."""
    odd = tmp_path / "odd"
    odd.mkdir()
    # Ending in a space; and on Linux, where a name is any bytes, not UTF-8.
    elsewhere = tmp_path / ("caf\udce9 " if sys.platform == "linux" else "café ")
    Git(odd).run("init", "-q", "-b", "main", f"--separate-git-dir={elsewhere}")
    _commit(odd, "a.txt")
    Git(odd).run("worktree", "add", "-q", "-b", "side", str(tmp_path / "odd2"))
    found = local.collect([tmp_path / "odd2", odd], datetime.now(UTC), 30, [elsewhere.as_posix()])
    assert [Path(r.dir) for r in found.repos] == [odd]


def test_my_commits_count_anywhere_on_a_branch_not_just_its_tip(
    tmp_path: Path, repo_templates: RepoTemplates
) -> None:
    repo = _clone(repo_templates, tmp_path)
    Git(repo).run("switch", "-q", "-c", "mine")
    _commit(repo, "m.txt")
    Git(repo).run("switch", "-q", "-c", "stacked")  # on mine, with someone else's commit on top
    (repo / "o.txt").write_text("o")
    Git(repo).run("add", "o.txt")
    Git(repo).run("-c", "user.email=other@example.com", "commit", "-q", "-m", "theirs")
    Git(repo).run("switch", "-q", "-c", "theirs", "main")
    (repo / "t.txt").write_text("t")
    Git(repo).run("add", "t.txt")
    Git(repo).run("-c", "user.email=other@example.com", "commit", "-q", "-m", "theirs")
    Git(repo).run("switch", "-q", "main")

    now = datetime.now(UTC) + timedelta(seconds=1)
    result = local.collect_repo(repo, now, 30, [])
    counted = {b.name: b.my_commits for b in result.branches}
    assert counted == {"main": None, "mine": 1, "stacked": 1, "theirs": 0}


def test_a_merge_counts_the_commits_of_both_its_sides(
    tmp_path: Path, repo_templates: RepoTemplates
) -> None:
    repo = _clone(repo_templates, tmp_path)
    Git(repo).run("switch", "-q", "-c", "side")
    (repo / "s.txt").write_text("s")
    Git(repo).run("add", "s.txt")
    Git(repo).run("-c", "user.email=other@example.com", "commit", "-q", "-m", "theirs")
    Git(repo).run("switch", "-q", "-c", "topic", "main")
    _commit(repo, "t.txt")
    Git(repo).run("merge", "-q", "--no-ff", "-m", "merge side", "side")
    Git(repo).run("switch", "-q", "main")

    result = local.collect_repo(repo, datetime.now(UTC) + timedelta(seconds=1), 30, [])
    counted = {b.name: (b.ahead_of_default, b.my_commits) for b in result.branches}
    assert counted == {"main": (None, None), "side": (1, 0), "topic": (3, 2)}


def test_a_branch_named_like_a_tag_is_counted_and_so_are_the_others(
    tmp_path: Path, repo_templates: RepoTemplates
) -> None:
    """git's short name for such a branch is `heads/v1`, which is no branch's ref."""
    repo = _clone(repo_templates, tmp_path)
    for branch in ("v1", "good"):
        Git(repo).run("switch", "-q", "-c", branch, "main")
        _commit(repo, f"{branch}.txt")
    Git(repo).run("tag", "v1", "main")
    Git(repo).run("switch", "-q", "--track", "-c", "v2", "main")  # tracks a local branch,
    Git(repo).run("tag", "v2", "main")
    Git(repo).run(
        "push", "-q", "origin", "refs/heads/v2"
    )  # so it's counted against its pushed copy
    Git(repo).run("switch", "-q", "main")

    result = local.collect_repo(repo, datetime.now(UTC) + timedelta(seconds=1), 30, [])
    counted = {b.name: (b.ahead_of_default, b.my_commits) for b in result.branches}
    assert counted["good"] == counted["v1"] == (1, 1)
    assert next(b for b in result.branches if b.name == "v2").upstream == "origin/v2"


def test_an_upstream_is_named_as_its_remote_branch_even_beside_a_branch_named_like_it(
    tmp_path: Path, repo_templates: RepoTemplates
) -> None:
    """git's short name for `origin/main` is `remotes/origin/main` while a branch is
    `origin/main` too."""
    repo = _clone(repo_templates, tmp_path)
    Git(repo).run("branch", "-q", "--no-track", "origin/main", "main")
    result = local.collect_repo(repo, datetime.now(UTC) + timedelta(seconds=1), 30, [])
    assert next(b for b in result.branches if b.name == "main").upstream == "origin/main"


def test_config_is_read_whatever_its_case_and_remotes_as_git_lists_them(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    Git(repo).run("remote", "add", "zeta", "https://github.com/acme/z.git")
    Git(repo).run("remote", "add", "alpha", "https://github.com/acme/a.git")
    Git(repo).run("config", "remote.Mid.pushurl", "https://github.com/acme/m.git")  # no url
    Git(repo).run("config", "branch.FeAture.pushRemote", "zeta")
    config = local.RepoConfig.read(repo)
    assert config.remotes() == Git(repo).run("remote").split() == ["Mid", "alpha", "zeta"]
    assert config.get("branch.FeAture.pushremote") == "zeta"
    assert config.get("branch.feature.pushremote") is None  # a branch's name keeps its case
    assert local.push_remote(repo, "FeAture") == "zeta"


def test_a_collect_starts_one_git_for_each_kind_of_thing_it_reads(
    tmp_path: Path, git_calls: list[GitCall], repo_templates: RepoTemplates
) -> None:
    """Not one for each remote, key, ref or branch: on Windows each start costs tens of ms."""
    repo = _clone(repo_templates, tmp_path)
    Git(repo).run("remote", "add", "fork", str(repo.parent / "origin"))
    Git(repo).run("fetch", "-q", "fork")
    for name in ("one", "two", "three"):
        Git(repo).run("switch", "-q", "-c", name, "main")
        _commit(repo, f"{name}.txt")
    git_calls.clear()  # those making the repos
    local.collect([repo], datetime.now(UTC) + timedelta(seconds=1), 30, [])
    assert sorted(args[0] for args in git_calls) == sorted(
        [
            "rev-parse",
            "worktree",
            "config",
            "for-each-ref",
            "--no-optional-locks",
            "rev-list",
            "log",
        ]
    )


def test_a_partial_collect_only_checks_the_git_directory_of_a_repo_it_keeps(
    tmp_path: Path, git_calls: list[GitCall]
) -> None:
    """Every refresh locates each configured checkout. It reads the moved repo's contents."""
    moved, kept = tmp_path / "moved", tmp_path / "kept"
    for repo in (moved, kept):
        _init_repo(repo)
        _commit(repo, "a.txt")
    now = datetime.now(UTC) + timedelta(seconds=1)
    before = local.collect([moved, kept], now, 30, [])
    common = git_common_dir(Git(moved))
    git_calls.clear()
    local.collect([moved], now, 30, [])
    alone = sorted(call[0] for call in git_calls)
    git_calls.clear()
    found = local.collect(
        [moved, kept], now, 30, [], subjects=frozenset({str(common)}), previous=before
    )
    assert sorted(call[0] for call in git_calls) == sorted([*alone, "rev-parse"])
    assert found.repos == before.repos


def test_a_repo_without_a_remote_is_collected(tmp_path: Path) -> None:
    repo = tmp_path / "local-only"
    _init_repo(repo)
    _commit(repo, "a.txt")
    Git(repo).run("checkout", "-q", "-b", "idea")
    collected = local.collect_repo(repo, datetime.now(UTC), 30, [])
    assert collected.github_repo is None and collected.my_dirs == ["."]  # from the local main
    assert [b.name for b in collected.branches] == ["idea", "main"]


def test_remotes_are_read_whatever_they_are_called(tmp_path: Path) -> None:
    """A fork: `upstream` is the org's repo, which main tracks, and `fork` is mine. Neither is
    `origin`."""
    org = tmp_path / "org"
    _init_repo(org)
    _commit(org, "a.txt")
    fork = tmp_path / "fork"
    Git(tmp_path).run("clone", "-q", str(org), str(fork))
    _commit(fork, "f.txt")  # on the fork's main only
    repo = tmp_path / "repo"
    Git(tmp_path).run("clone", "-q", "-o", "upstream", str(org), str(repo))
    Git(repo).run("remote", "add", "fork", str(fork))
    Git(repo).run("fetch", "-q", "fork")
    Git(repo).run("switch", "-q", "--no-track", "-c", "synced", "fork/main")  # all on a remote
    Git(repo).run("switch", "-q", "-c", "mine", "main")
    _commit(repo, "m.txt")
    Git(repo).run("switch", "-q", "main")
    Git(repo).run("remote", "set-url", "upstream", "git@github.com:acme/widgets.git")
    Git(repo).run("remote", "set-url", "fork", "https://github.com/me/widgets.git")

    found = local.read_remotes(repo)
    assert found.default_branch == "main" and found.github_repo == "acme/widgets"
    assert sorted(found.default_refs) == ["fork/main", "upstream/main"]
    now = datetime.now(UTC) + timedelta(seconds=1)
    collected = local.collect_repo(repo, now, 30, [])
    branches = {b.name: b for b in collected.branches}
    assert branches["mine"].ahead_of_default == 1 and branches["mine"].my_commits == 1
    assert branches["synced"].ahead_of_default == 0 and branches["synced"].my_commits == 0
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    LocalRepository(db).save(LocalSnapshot(repos=[collected]))
    assert LocalRepository(db).load().repos[0].default_refs == collected.default_refs


def test_reading_a_worktrees_status_leaves_the_index_alone(tmp_path: Path) -> None:
    """A plain `git status` rewrites the index under its lock, which would make a git
    command run meanwhile fail."""
    repo = tmp_path / "r"
    _init_repo(repo)
    (repo / "a.py").write_text("x\n")
    Git(repo).run("add", "a.py")
    Git(repo).run("commit", "-q", "-m", "a")
    os.utime(repo / "a.py", ns=(1, 1))  # same content, new stat: the index is stale
    (repo / "b.py").write_text("y\n")
    index = repo / ".git" / "index"
    before = index.stat()
    assert local.worktree_status(repo) == (0, 1)
    after = index.stat()
    assert (after.st_mtime_ns, after.st_ino) == (before.st_mtime_ns, before.st_ino)


def test_one_repo_git_cant_read_doesnt_fail_the_rest(tmp_path: Path) -> None:
    good = tmp_path / "good"
    _init_repo(good)
    _commit(good, "a.txt")
    gone = tmp_path / "gone-wt"
    Git(good).run("worktree", "add", "-q", "-b", "wt", str(gone))
    shutil.rmtree(gone)  # deleted without `git worktree remove`
    plain = tmp_path / "plain"  # a directory, but no repository
    plain.mkdir()
    now = datetime.now(UTC)
    found = local.collect([plain, good], now, 30, [])
    assert [r.dir for r in found.repos] == [str(good)]
    assert [Path(w.path) for w in found.repos[0].worktrees] == [good]  # git writes / on Windows
    earlier = found.repos[0].model_copy(update={"dir": str(plain)})
    kept = local.collect([plain, good], now, 30, [], previous=LocalSnapshot(repos=[earlier]))
    assert [r.dir for r in kept.repos] == [str(plain), str(good)]  # as read before


def test_when_no_repo_can_be_read_the_collect_fails(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(GitError):
        local.collect([plain], datetime.now(UTC), 30, [])


def test_a_repo_on_master_with_no_remote_head_counts_against_master(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    Git(origin).run("init", "-q", "--bare", "-b", "master")
    repo = tmp_path / "repo"
    repo.mkdir()
    Git(repo).run("init", "-q", "-b", "master")
    _commit(repo, "a.txt")
    Git(repo).run("remote", "add", "origin", str(origin))
    Git(repo).run("push", "-q", "-u", "origin", "master")  # sets no origin/HEAD
    Git(repo).run("switch", "-q", "-c", "topic")
    _commit(repo, "b.txt")
    Git(repo).run("switch", "-q", "master")
    found = local.collect_repo(repo, datetime.now(UTC) + timedelta(seconds=1), 30, [])
    assert (found.default_branch, found.default_refs) == ("master", ["origin/master"])
    counts = {b.name: (b.ahead_of_default, b.my_commits) for b in found.branches}
    assert counts == {"master": (None, None), "topic": (1, 1)}


def test_with_nothing_to_count_against_nothing_is_counted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    Git(repo).run("init", "-q", "-b", "trunk")
    _commit(repo, "a.txt")
    found = local.collect_repo(repo, datetime.now(UTC) + timedelta(seconds=1), 30, [])
    assert found.default_refs == []
    assert [(b.ahead_of_default, b.my_commits) for b in found.branches] == [(None, None)]


def test_the_dirs_i_work_in_are_by_the_repos_email_exactly_and_posix(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    Git(repo).run("config", "user.email", "an@x.com")
    _commit(repo, "api/v1/mine.py")
    Git(repo).run("-c", "user.email=jan@x.com", "commit", "-q", "--allow-empty", "-m", "x")
    (repo / "web").mkdir()
    (repo / "web" / "theirs.ts").write_text("t")
    Git(repo).run("add", "web")
    Git(repo).run("-c", "user.email=jan@x.com", "commit", "-q", "-m", "theirs")
    found = local.collect_repo(repo, datetime.now(UTC) + timedelta(seconds=1), 30, [])
    assert found.my_dirs == ["api/v1"]  # not jan@x.com's web, whose email has mine in it


def _hangs(monkeypatch: pytest.MonkeyPatch, hung: Path, command: str) -> list[str]:
    """git times out on `command` in `hung`; returns the commands each run there."""
    ran: list[str] = []
    real = Git.process

    def process(self: Git, *args: Arg, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if Path(self.directory) == hung:
            first = os.fsdecode(args[0])
            ran.append(first)
            if first == command:
                raise GitTimeoutError([os.fsdecode(arg) for arg in args], None, "")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Git, "process", process)
    return ran


@pytest.mark.parametrize("command", ["rev-parse", "rev-list", "worktree"])
def test_a_repo_whose_git_times_out_is_kept_as_read_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """A timeout reading metadata, worktrees or branch counts keeps the previous checkout."""
    hung, good = tmp_path / "hung", tmp_path / "good"
    for repo in (hung, good):
        _init_repo(repo)
        _commit(repo, "a.txt")
        Git(repo).run("switch", "-q", "-c", "topic")
        _commit(repo, "b.txt")
    now = datetime.now(UTC) + timedelta(seconds=1)
    before = local.collect([hung, good], now, 30, [])
    ran = _hangs(monkeypatch, hung, command)
    found = local.collect([hung, good], now, 30, [], previous=before)
    assert found.repos == before.repos
    assert ran.count(command) == 1


@pytest.mark.parametrize("ignore_main", [False, True])
def test_a_failed_main_checkout_keeps_its_identity_and_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ignore_main: bool
) -> None:
    main, clone, linked = tmp_path / "main", tmp_path / "clone", tmp_path / "linked"
    for repo in (main, clone):
        _init_repo(repo)
        _commit(repo, "first.txt")
        Git(repo).run("remote", "add", "origin", "https://github.com/acme/widgets.git")
    Git(main).run("worktree", "add", "-q", "-b", "side", str(linked))
    now = datetime.now(UTC) + timedelta(seconds=1)
    repos = [main, clone, linked]
    globs = [f"*/{main.name}"] if ignore_main else []
    before = local.collect(repos, now, 30, globs)
    _commit(linked, "new.txt")
    _hangs(monkeypatch, main, "rev-parse")

    found = local.collect(repos, now, 30, globs, previous=before)

    assert [r.dir for r in found.repos] == [str(main), str(clone)]
    visible = [linked] if ignore_main else [main, linked]
    assert [Path(wt.path).resolve() for wt in found.repos[0].worktrees] == [
        path.resolve() for path in visible
    ]
    assert found.repos[0].branches != before.repos[0].branches


def test_config_bytes_that_arent_utf8_are_replaced_so_the_repo_can_be_stored(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    Git(repo).run("init", "-q", "-b", "dev")
    _commit(repo, "a.txt")
    with (repo / ".git" / "config").open("ab") as config:
        config.write(b"[init]\n\tdefaultBranch = tr\xe9s\n")  # neither main nor master here
        config.write(b'[remote "r\xe9mote"]\n\turl = /nowhere\n')
    # A remote's name, from its key, is what a stored default ref (`<remote>/<branch>`) starts with.
    assert local.RepoConfig.read(repo).remotes() == ["r\ufffdmote"]
    collected = local.collect_repo(repo, datetime.now(UTC), 30, [])
    assert collected.default_branch == "tr\ufffds"
    db = Database(":memory:")
    db.migrate("git", MIGRATIONS)
    LocalRepository(db).save(LocalSnapshot(repos=[collected]))


def test_a_repo_whose_git_hangs_doesnt_fail_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hung, good = tmp_path / "hung", tmp_path / "good"
    for repo in (hung, good):
        _init_repo(repo)
        _commit(repo, "a.txt")
    real = local.collect_repo

    def hanging(repo: Path, *args: Any, **kwargs: Any) -> LocalRepo:
        if repo == hung:
            raise GitTimeoutError(["status"], None, "")
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(local, "collect_repo", hanging)
    found = local.collect([hung, good], datetime.now(UTC), 30, [])
    assert [r.dir for r in found.repos] == [str(good)]
