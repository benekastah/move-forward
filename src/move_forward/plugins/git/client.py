"""Local git collector: worktrees, branches and the directories I work in."""

import fnmatch
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from ...shell import CommandError, git
from .models import Branch, LocalRepo, LocalSnapshot, Worktree

_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
_TRACK_RE = re.compile(r"ahead (\d+)|behind (\d+)|(gone)")
_FOR_EACH_REF_FORMAT = (
    "%(refname:short)%09%(upstream:short)%09%(upstream:track)%09%(committerdate:iso-strict)"
    "%09%(objectname)"
)
FALLBACK_DEFAULT_BRANCH = "main"


def parse_worktrees(porcelain: str) -> list[tuple[str, str | None]]:
    """(path, branch) pairs from `git worktree list --porcelain`; branch is None when detached."""
    worktrees: list[tuple[str, str | None]] = []
    path: str | None = None
    branch: str | None = None
    for line in porcelain.splitlines() + [""]:
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/") :]
        elif not line and path is not None:
            worktrees.append((path, branch))
            path, branch = None, None
    return worktrees


def parse_status(porcelain: str) -> tuple[int, int]:
    """(modified, untracked) counts from `git status --porcelain`."""
    lines = [line for line in porcelain.splitlines() if line]
    untracked = sum(1 for line in lines if line.startswith("??"))
    return len(lines) - untracked, untracked


def parse_branches(for_each_ref: str) -> list[Branch]:
    branches: list[Branch] = []
    for line in for_each_ref.splitlines():
        name, upstream, track, date, sha = (line.split("\t") + ["", "", "", "", ""])[:5]
        if not name or not date:
            continue
        ahead = behind = 0
        gone = False
        for match in _TRACK_RE.finditer(track):
            if match.group(1):
                ahead = int(match.group(1))
            elif match.group(2):
                behind = int(match.group(2))
            elif match.group(3):
                gone = True
        branches.append(
            Branch(
                name=name,
                sha=sha or None,
                upstream=upstream or None,
                ahead=ahead,
                behind=behind,
                gone=gone,
                committed_at=datetime.fromisoformat(date),
            )
        )
    return branches


def github_repo(remote_url: str) -> str | None:
    match = _GITHUB_REMOTE_RE.search(remote_url.strip())
    return match.group(1) if match else None


def _default_branch(repo: Path) -> str:
    try:
        ref = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    except CommandError:
        return FALLBACK_DEFAULT_BRANCH
    return ref.removeprefix("origin/")


def _my_dirs(repo: Path, default_branch: str, email: str, since_days: int) -> list[str]:
    """Directories with commits of mine on the default branch within `since_days`."""
    out = git(
        repo,
        "log",
        f"origin/{default_branch}",
        f"--since={since_days} days ago",
        f"--author={email}",
        "--name-only",
        "--format=",
    )
    return sorted({str(Path(path).parent) for path in out.splitlines() if path})


def collect_repo(
    repo: Path,
    email: str,
    now: datetime,
    commit_history_days: int,
    ignored_worktree_globs: list[str],
) -> LocalRepo:
    default_branch = _default_branch(repo)
    worktrees: list[Worktree] = []
    for path, branch in parse_worktrees(git(repo, "worktree", "list", "--porcelain")):
        if any(fnmatch.fnmatch(path, glob) for glob in ignored_worktree_globs):
            continue
        modified, untracked = parse_status(git(Path(path), "status", "--porcelain"))
        worktrees.append(Worktree(path=path, branch=branch, modified=modified, untracked=untracked))

    branches = parse_branches(
        git(repo, "for-each-ref", "refs/heads", f"--format={_FOR_EACH_REF_FORMAT}")
    )
    recent = now - timedelta(days=commit_history_days)
    with_counts: list[Branch] = []
    for branch in branches:
        if (
            branch.upstream is None
            and branch.name != default_branch
            and branch.committed_at >= recent
        ):
            try:
                count = int(
                    git(repo, "rev-list", "--count", f"origin/{default_branch}..{branch.name}")
                )
            except CommandError:
                count = None
            branch = branch.model_copy(update={"ahead_of_default": count})
        with_counts.append(branch)

    try:
        remote = github_repo(git(repo, "remote", "get-url", "origin"))
    except CommandError:
        remote = None
    return LocalRepo(
        dir=str(repo),
        github_repo=remote,
        default_branch=default_branch,
        worktrees=worktrees,
        branches=with_counts,
        my_dirs=_my_dirs(repo, default_branch, email, commit_history_days),
    )


def my_email(repo: Path) -> str:
    return git(repo, "config", "user.email").strip()


def collect(
    repos: list[Path],
    now: datetime,
    commit_history_days: int,
    ignored_worktree_globs: list[str],
) -> LocalSnapshot:
    email = my_email(repos[0]) if repos else ""
    existing = [repo for repo in repos if repo.exists()]

    def one(repo: Path) -> LocalRepo:
        return collect_repo(repo, email, now, commit_history_days, ignored_worktree_globs)

    with ThreadPoolExecutor(max_workers=max(1, len(existing))) as pool:
        return LocalSnapshot(repos=list(pool.map(one, existing)))
