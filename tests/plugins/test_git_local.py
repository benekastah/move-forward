from datetime import UTC, datetime, timedelta
from pathlib import Path

from move_forward.plugins.git import client as local
from move_forward.shell import git

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


def _init_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    _configure(path)


def _configure(path: Path) -> None:
    git(path, "config", "user.email", "me@example.com")
    git(path, "config", "user.name", "Me")
    git(path, "config", "commit.gpgsign", "false")


def _commit(path: Path, name: str) -> None:
    (path / name).parent.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(name)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", name)


def test_collect_repo_against_a_real_repo(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _init_repo(origin)
    _commit(origin, "src/a.txt")
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    _configure(repo)
    git(repo, "switch", "-q", "-c", "feature")
    _commit(repo, "b.txt")
    (repo / "dirty.txt").write_text("x")

    now = datetime.now(UTC) + timedelta(seconds=1)
    result = local.collect_repo(repo, "me@example.com", now, 30, ["*/ignored/*"])
    assert result.default_branch == "main"
    assert result.github_repo is None
    assert [(w.branch, w.modified, w.untracked) for w in result.worktrees] == [("feature", 0, 1)]
    feature = next(b for b in result.branches if b.name == "feature")
    assert feature.upstream is None and feature.ahead_of_default == 1
    assert feature.sha == git(repo, "rev-parse", "feature").strip()
    assert result.my_dirs == ["src"]
