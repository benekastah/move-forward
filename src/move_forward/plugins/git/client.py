"""Local git collector: worktrees, branches and the directories I work in."""

import fnmatch
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ...shell import CommandError, git
from . import changes
from .models import Branch, LocalRepo, LocalSnapshot, Worktree

_REMOTE_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]+@)?([^/:\s]+)(?::\d+)?[:/]"
    r"([\w.-]+/[\w.-]+?)(?:\.git)?/?$"
)
"""A remote URL's host and `owner/name`: `https://host/o/n`, `ssh://git@host:22/o/n`,
`git@host:o/n.git`, or `alias:o/n` (a host from ~/.ssh/config)."""
GITHUB_HOSTS = ("github.com",)
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


def github_repo(remote_url: str, hosts: Sequence[str] = GITHUB_HOSTS) -> str | None:
    """The `owner/name` of a remote on one of `hosts` (as the remote names it: github.com,
    an Enterprise server, or an SSH alias for either); None for any other remote."""
    match = _REMOTE_RE.match(remote_url.strip())
    if match is None or match.group(1).lower() not in {h.lower() for h in hosts}:
        return None
    return match.group(2)


@dataclass(frozen=True)
class Remotes:
    """What a repository's remotes say, whatever they're called (`origin`, `upstream`, a
    fork's own name)."""

    default_branch: str
    """The default branch's name, from the main remote's `HEAD`."""
    default_refs: list[str]
    """The default branch on every remote that has one (`origin/main`, `upstream/main`),
    or the local default branch when no remote does: a commit on none of them isn't on the
    default branch yet."""
    github_repo: str | None
    """`owner/name` of the main remote on GitHub, else of the first remote on GitHub."""


def remotes(repo: Path) -> list[str]:
    """The repository's remotes, by name, in the order git lists them."""
    return git(repo, "remote").split()


def _remote_head(repo: Path, remote: str) -> str | None:
    """The branch `remote`'s `HEAD` points at, e.g. `main`; None when git doesn't know."""
    try:
        ref = git(repo, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD").strip()
    except CommandError:
        return None
    return ref.removeprefix(f"{remote}/")


def _config(repo: Path, key: str) -> str | None:
    try:
        return git(repo, "config", "--get", key).strip() or None
    except CommandError:  # git exits 1 for a key that isn't set
        return None


def _ref_exists(repo: Path, ref: str) -> bool:
    try:
        git(repo, "rev-parse", "--verify", "--quiet", ref)
    except CommandError:
        return False
    return True


def read_remotes(repo: Path, hosts: Sequence[str] = GITHUB_HOSTS) -> Remotes:
    """The repository's default branch and GitHub repo, from all its remotes. The main
    remote is the one the local default branch tracks (`upstream` in a fork, often), else
    the first whose `HEAD` git knows."""
    names = remotes(repo)
    heads = {name: head for name in names if (head := _remote_head(repo, name))}
    main = next(
        (name for name, head in heads.items() if _config(repo, f"branch.{head}.remote") == name),
        next(iter(heads), names[0] if names else None),
    )
    default_branch = heads.get(main or "", FALLBACK_DEFAULT_BRANCH)
    default_refs = [
        f"{name}/{heads.get(name, default_branch)}"
        for name in names
        if _ref_exists(repo, f"refs/remotes/{name}/{heads.get(name, default_branch)}")
    ]
    if not default_refs and _ref_exists(repo, f"refs/heads/{default_branch}"):
        default_refs = [f"refs/heads/{default_branch}"]
    urls = {name: _config(repo, f"remote.{name}.url") or "" for name in names}
    on_github = [
        r for name in [main, *names] if name and (r := github_repo(urls.get(name, ""), hosts))
    ]
    return Remotes(default_branch, default_refs, on_github[0] if on_github else None)


def push_remote(repo: Path, branch: str) -> str | None:
    """Where a branch without an upstream should be pushed, as git would choose: its push
    remote, `remote.pushDefault`, its remote, or the only remote there is. None when there
    are several and nothing says which."""
    for key in (f"branch.{branch}.pushRemote", "remote.pushDefault", f"branch.{branch}.remote"):
        if (name := _config(repo, key)) and name != ".":
            return name
    names = remotes(repo)
    return names[0] if len(names) == 1 else None


def push_target(repo: Path, branch: str) -> tuple[str, str]:
    """Where to push a branch that has an upstream: git's push remote for it (its
    `pushRemote`, `remote.pushDefault`, else its upstream's remote) and, there, its
    upstream's branch when that's the same remote, else a branch of its own name."""
    out = git(
        repo,
        "for-each-ref",
        "--format=%(push:remotename)%09%(upstream:remotename)%09%(upstream:remoteref)",
        f"refs/heads/{branch}",
    )
    remote, upstream_remote, upstream_ref = out.strip("\n").split("\t")
    if remote == upstream_remote and upstream_ref:
        return remote, upstream_ref
    return remote or upstream_remote, f"refs/heads/{branch}"


def _my_dirs(repo: Path, default_refs: list[str], email: str, since_days: int) -> list[str]:
    """Directories with commits of mine on the default branch within `since_days`."""
    if not default_refs:
        return []
    out = git(
        repo,
        "log",
        *default_refs,
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
    github_hosts: Sequence[str] = GITHUB_HOSTS,
) -> LocalRepo:
    found = read_remotes(repo, github_hosts)
    default_branch = found.default_branch
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
    try:
        repo_email = my_email(repo)  # a repo can have its own, e.g. a personal project's
    except CommandError:
        repo_email = ""
    with_counts: list[Branch] = []
    for branch in branches:
        if (
            branch.upstream is None
            and branch.name != default_branch
            and branch.committed_at >= recent
        ):
            try:
                count = int(
                    git(
                        repo,
                        "rev-list",
                        "--count",
                        f"refs/heads/{branch.name}",
                        "--not",
                        *found.default_refs,
                    )
                )
            except CommandError:
                count = None
            branch = branch.model_copy(update={"ahead_of_default": count})
        if branch.name != default_branch and branch.committed_at >= recent:
            mine = _my_commits(repo, found.default_refs, branch.name, repo_email or email)
            branch = branch.model_copy(update={"my_commits": mine})
        with_counts.append(branch)

    return LocalRepo(
        dir=str(repo),
        github_repo=found.github_repo,
        default_branch=default_branch,
        default_refs=found.default_refs,
        worktrees=worktrees,
        branches=with_counts,
        my_dirs=_my_dirs(repo, found.default_refs, email, commit_history_days),
    )


def _my_commits(repo: Path, default_refs: list[str], branch: str, email: str) -> int | None:
    """My commits on `branch` that aren't on the default branch (on any remote), by my email
    (taken as a fixed string, not a pattern); None when git can't say."""
    if not email:
        return None
    try:
        return int(
            git(
                repo,
                "rev-list",
                "--count",
                "--fixed-strings",
                f"--author=<{email}>",
                f"refs/heads/{branch}",
                "--not",
                *default_refs,
            )
        )
    except CommandError:
        return None


def main_worktree(repo: Path) -> Path:
    """The repository's main checkout: `git worktree list` names it first."""
    [(path, _), *_] = parse_worktrees(git(repo, "worktree", "list", "--porcelain"))
    return Path(path).resolve()


def folded(repos: list[Path]) -> list[Path]:
    """`repos` with one path per repository: a path that's a worktree of another listed one
    is folded into it, keeping the main checkout if it's listed, else the first listed."""
    by_main: dict[Path, Path] = {}
    for repo in repos:
        main = main_worktree(repo)
        if main not in by_main or repo.resolve() == main:
            by_main[main] = repo
    return list(by_main.values())


def my_email(repo: Path) -> str:
    return git(repo, "config", "user.email").strip()


def collect(
    repos: list[Path],
    now: datetime,
    commit_history_days: int,
    ignored_worktree_globs: list[str],
    only: set[str] | None = None,
    previous: LocalSnapshot | None = None,
    github_hosts: Sequence[str] = GITHUB_HOSTS,
) -> LocalSnapshot:
    """Every repo among `repos`, or with `only` (common git directories, see
    `changes.fingerprints`) just those, keeping the rest as `previous` has them; a repo
    `previous` lacks is read anyway."""
    email = my_email(repos[0]) if repos else ""
    existing = folded([repo for repo in repos if repo.exists()])
    kept = {r.dir: r for r in previous.repos} if previous and only is not None else {}

    def one(repo: Path) -> LocalRepo:
        if str(repo) in kept and str(changes.common_dir(repo)) not in (only or set()):
            return kept[str(repo)]
        return collect_repo(
            repo, email, now, commit_history_days, ignored_worktree_globs, github_hosts
        )

    with ThreadPoolExecutor(max_workers=max(1, len(existing))) as pool:
        return LocalSnapshot(repos=list(pool.map(one, existing)))
