"""Local git collector: worktrees, branches and the directories I work in."""

import fnmatch
import logging
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import repeat
from pathlib import Path, PurePosixPath

from move_forward.api.git import (
    Git,
    GitCache,
    GitError,
    NoAnswerError,
    config,
    git_common_dir,
    git_dir,
    printable,
    refs,
)
from move_forward.api.ids import branch_parts
from move_forward.plugins.git.models import Branch, LocalRepo, LocalSnapshot, Worktree

log = logging.getLogger(__name__)

_REMOTE_RE = re.compile(
    r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/\s]+@)?([^/:\s]+)(?::\d+)?[:/]"
    r"([\w.-]+/[\w.-]+?)(?:\.git)?/?$"
)
"""A remote URL's host and `owner/name`: `https://host/o/n`, `ssh://git@host:22/o/n`,
`git@host:o/n.git`, or `alias:o/n` (a host from ~/.ssh/config)."""
GITHUB_HOSTS = ("github.com",)
_TRACK_RE = re.compile(r"ahead (\d+)|behind (\d+)|(gone)")
_FOR_EACH_REF_FORMAT = (
    "%(refname:lstrip=2)%09%(upstream:lstrip=2)%09%(upstream:track)%09%(committerdate:iso-strict)"
    "%09%(objectname)%09%(upstream:remotename)"
)
"""A branch's fields. Its name and its upstream's (`origin/main`) are their refs without
`refs/heads/` or `refs/remotes/`: `:short` would make a branch named like a tag `heads/v1`."""
_REFS_FORMAT = f"%(refname)%09%(symref)%09{_FOR_EACH_REF_FORMAT}"
"""`Refs.read`'s: each ref's full name and what it points at if it's symbolic, then a branch's
fields as `parse_branches` reads them."""
FALLBACK_DEFAULT_BRANCH = "main"
METADATA_TIMEOUT_SECONDS = 1.0
"""How long a change check or watcher setup waits for a Git metadata command."""


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
    """(modified, untracked) counts from `git status --porcelain --untracked-files=all`."""
    lines = [line for line in porcelain.splitlines() if line]
    untracked = sum(1 for line in lines if line.startswith("??"))
    return len(lines) - untracked, untracked


def worktree_status(worktree: Path) -> tuple[int, int]:
    """A worktree's (modified, untracked) counts. Without taking git's optional locks: a
    plain `git status` refreshes the index under `index.lock`, and a git command of mine
    run meanwhile would fail on it."""
    return parse_status(
        Git(worktree).run(
            "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", strip=False
        )
    )


def parse_branches(for_each_ref: str) -> list[Branch]:
    branches: list[Branch] = []
    for line in for_each_ref.splitlines():
        name, upstream, track, date, sha, remote = (line.split("\t") + [""] * 6)[:6]
        if not name or not date:
            continue
        if remote == ".":
            # Tracking a local branch (`--track main`): nothing of it is on a remote, so it
            # has no upstream as far as pushing goes, and its counts aren't "unpushed".
            upstream, track = "", ""
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


def local_tracking(for_each_ref: str) -> set[str]:
    """The branches tracking a local branch (`--track main`), in `for-each-ref`'s output with
    `_FOR_EACH_REF_FORMAT`."""
    return {
        fields[0]
        for line in for_each_ref.splitlines()
        if len(fields := line.split("\t")) >= 6 and fields[5] == "."
    }


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
    """The default branch's name, from the main remote's `HEAD`, else the local one."""
    default_refs: list[str]
    """The default branch on every remote that has one (`origin/main`, `upstream/main`),
    or the local default branch when no remote does: a commit on none of them isn't on the
    default branch yet. Empty when there's none at all, and nothing is counted against it."""
    github_repo: str | None
    """`owner/name` of the main remote on GitHub, else of the first remote on GitHub."""


@dataclass(frozen=True)
class RepoConfig:
    """A repository's git config as git reads it (its own, mine and the system's), from one
    `git config --list`."""

    values: dict[str, str]
    """Each key's last value, by the key as git lists it: its section and name
    lower-cased, a subsection (a branch's or a remote's name) as it is. A key set without a
    value has an empty one."""

    @classmethod
    def read(cls, repo: Path) -> RepoConfig:
        # Keys too: a remote's name, from its key, is stored, and a branch's is looked up as
        # `Refs` read it.
        listed = config(Git(repo)).items()
        return cls({printable(key): printable(values[-1] or "") for key, values in listed})

    def get(self, key: str) -> str | None:
        """`key`'s value, using the key as git lists it, or None when it's unset or empty."""
        return self.values.get(key) or None

    def remotes(self) -> list[str]:
        """The remotes, by name, as `git remote` lists them: each with a key of its own, sorted."""
        return sorted(
            {
                key.removeprefix("remote.").rpartition(".")[0]
                for key in self.values
                if key.startswith("remote.") and key.count(".") >= 2
            }
        )


@dataclass(frozen=True)
class Refs:
    """A repository's branches and remote-tracking branches, from one `git for-each-ref`."""

    names: set[str]
    """Every ref's full name (`refs/remotes/origin/main`)."""
    targets: dict[str, str]
    """What each symbolic ref points at, by full name (`refs/remotes/origin/HEAD`)."""
    branches: str
    """The branches' lines, as `parse_branches` reads them."""

    @classmethod
    def read(cls, repo: Path) -> Refs:
        names: set[str] = set()
        targets: dict[str, str] = {}
        branches: list[str] = []
        listed = printable(
            Git(repo).run(
                "for-each-ref",
                f"--format={_REFS_FORMAT}",
                "refs/heads",
                "refs/remotes",
                strip=False,
            )
        )
        for line in listed.splitlines():
            name, target, fields = (line.split("\t", 2) + ["", ""])[:3]
            names.add(name)
            if target:
                targets[name] = target
            if name.startswith("refs/heads/"):
                branches.append(fields)
        return cls(names, targets, "\n".join(branches))

    def remote_head(self, remote: str) -> str | None:
        """The branch `remote`'s `HEAD` points at, e.g. `main`; None when git doesn't know."""
        target = self.targets.get(f"refs/remotes/{remote}/HEAD")
        return target.removeprefix(f"refs/remotes/{remote}/") if target else None


def _local_default(config: RepoConfig, refs: Refs) -> str:
    """The default branch when no remote's `HEAD` says: `init.defaultBranch`, `main` or
    `master`, the first there is locally."""
    configured = config.get("init.defaultbranch")
    for name in [*([configured] if configured else []), FALLBACK_DEFAULT_BRANCH, "master"]:
        if f"refs/heads/{name}" in refs.names:
            return name
    return configured or FALLBACK_DEFAULT_BRANCH


def read_remotes(repo: Path, hosts: Sequence[str] = GITHUB_HOSTS) -> Remotes:
    """The repository's default branch and GitHub repo, from all its remotes (see `_remotes`)."""
    return _remotes(RepoConfig.read(repo), Refs.read(repo), hosts)


def _remotes(config: RepoConfig, refs: Refs, hosts: Sequence[str]) -> Remotes:
    """The main remote is the one the local default branch tracks (`upstream` in a fork,
    often), else the first whose `HEAD` git knows. With no `HEAD` known (no remotes, or one
    pushed to without `git remote set-head`), the default branch is the local one
    (`_local_default`)."""
    names = config.remotes()
    heads = {name: head for name in names if (head := refs.remote_head(name))}
    main = next(
        (name for name, head in heads.items() if config.get(f"branch.{head}.remote") == name),
        next(iter(heads), names[0] if names else None),
    )
    default_branch = heads.get(main or "") or _local_default(config, refs)
    default_refs = [
        f"{name}/{heads.get(name, default_branch)}"
        for name in names
        if f"refs/remotes/{name}/{heads.get(name, default_branch)}" in refs.names
    ]
    if not default_refs and f"refs/heads/{default_branch}" in refs.names:
        default_refs = [f"refs/heads/{default_branch}"]
    on_github = [
        r
        for name in [main, *names]
        if name and (r := github_repo(config.get(f"remote.{name}.url") or "", hosts))
    ]
    return Remotes(default_branch, default_refs, on_github[0] if on_github else None)


def push_remote(repo: Path, branch: str) -> str | None:
    """Where a branch without an upstream should be pushed, as git would choose (see
    `_push_remote`)."""
    return _push_remote(RepoConfig.read(repo), branch)


def _push_remote(config: RepoConfig, branch: str) -> str | None:
    """Its push remote, `remote.pushDefault`, its remote, or the only remote there is. None
    when there are several and nothing says which."""
    for key in (f"branch.{branch}.pushremote", "remote.pushdefault", f"branch.{branch}.remote"):
        if (name := config.get(key)) and name != ".":
            return name
    names = config.remotes()
    return names[0] if len(names) == 1 else None


def push_target(repo: Path, branch: str) -> tuple[str, str]:
    """Where to push a branch that has an upstream: git's push remote for it (its
    `pushRemote`, `remote.pushDefault`, else its upstream's remote) and, there, its
    upstream's branch when that's the same remote, else a branch of its own name. One tracking
    a local branch goes to the remote `push_remote` picks, under its own name; the remote is
    empty when there's none."""
    fields = ("push:remotename", "upstream:remotename", "upstream:remoteref")
    found = refs(Git(repo), f"refs/heads/{branch}", fields=fields)[f"refs/heads/{branch}"]
    remote, upstream_remote, upstream_ref = (found.fields[field] for field in fields)
    if upstream_remote == "." and remote in ("", "."):
        # Its upstream is a local branch: pushing there would move that branch. Empty when
        # there's no remote to push to instead.
        return push_remote(repo, branch) or "", f"refs/heads/{branch}"
    if remote == upstream_remote and upstream_ref:
        return remote, upstream_ref
    return remote or upstream_remote, f"refs/heads/{branch}"


def _my_dirs(repo: Path, default_refs: list[str], email: str, since_days: int) -> list[str]:
    """Directories with commits of mine (by my email, as a fixed string) on the default branch
    within `since_days`, as git names them: repo-relative and POSIX, on every system."""
    if not default_refs or not email:
        return []
    out = printable(
        Git(repo).run(
            "log",
            *default_refs,
            f"--since={since_days} days ago",
            "--fixed-strings",
            f"--author=<{email}>",
            "--name-only",
            "--format=",
            strip=False,
        )
    )
    return sorted({str(PurePosixPath(path).parent) for path in out.splitlines() if path})


def collect_repo(
    repo: Path,
    now: datetime,
    commit_history_days: int,
    ignored_worktree_globs: list[str],
    github_hosts: Sequence[str] = GITHUB_HOSTS,
    *,
    listed: list[tuple[str, str | None]] | None = None,
) -> LocalRepo:
    """`repo` as git has it, reusing its worktree listing when already read."""
    config, refs = RepoConfig.read(repo), Refs.read(repo)
    found = _remotes(config, refs, github_hosts)
    default_branch = found.default_branch
    worktrees: list[Worktree] = []
    for path, branch in read_worktrees(repo) if listed is None else listed:
        if any(fnmatch.fnmatch(path, glob) for glob in ignored_worktree_globs):
            continue
        if not Path(path).exists():  # deleted without `git worktree remove`: git lists it
            continue
        modified, untracked = worktree_status(Path(path))
        worktrees.append(Worktree(path=path, branch=branch, modified=modified, untracked=untracked))

    tracking = local_tracking(refs.branches)
    branches = [
        _pushed_copy(repo, b, config, refs) if b.name in tracking else b
        for b in parse_branches(refs.branches)
    ]
    recent = now - timedelta(days=commit_history_days)
    email = config.get("user.email") or ""  # a repo can have its own, e.g. a personal project's
    # Without a default branch to count against, there's nothing to count.
    to_count = {
        b.name
        for b in branches
        if found.default_refs
        and b.name != default_branch
        and (b.gone or (b.upstream is None and b.committed_at >= recent))
    }
    mine = {b.name for b in branches if b.name != default_branch and b.committed_at >= recent}
    counts = _off_default(repo, [b for b in branches if b.name in to_count | mine], found, email)
    with_counts: list[Branch] = []
    for branch in branches:
        ahead, my_commits = counts.get(branch.name, (None, None))
        if branch.name in to_count:
            branch = branch.model_copy(update={"ahead_of_default": ahead})
        if branch.name in mine:
            branch = branch.model_copy(update={"my_commits": my_commits})
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


def _off_default(
    repo: Path, branches: list[Branch], found: Remotes, email: str
) -> dict[str, tuple[int | None, int | None]]:
    """Each of `branches`' commits that aren't on the default branch (on any remote), and of
    those mine, by my email exactly (None without one), from one walk of them all; both None
    when git can't say, and empty without a default branch to count against."""
    if not branches or not found.default_refs:
        return {}
    tips = [b.sha for b in branches if b.sha]
    try:
        walked = printable(
            Git(repo).run(
                "rev-list",
                "--format=%H %P%x09%ae",
                *tips,
                "--not",
                *found.default_refs,
                strip=False,
            )
        )
    except GitError:
        return {b.name: (None, None) for b in branches}
    parents: dict[str, list[str]] = {}
    authors: dict[str, str] = {}
    for line in walked.splitlines():
        if line.startswith("commit "):  # rev-list's header above each, which `--format` keeps
            continue
        shas, _, author = line.partition("\t")
        sha, *of = shas.split()
        parents[sha], authors[sha] = of, author
    counts: dict[str, tuple[int | None, int | None]] = {}
    for branch in branches:
        reached: set[str] = set()
        todo = [branch.sha] if branch.sha in parents else []
        while todo:
            sha = todo.pop()
            if sha not in reached:
                reached.add(sha)
                todo.extend(p for p in parents[sha] if p in parents)
        mine = sum(1 for sha in reached if authors[sha] == email) if email else None
        counts[branch.name] = (len(reached), mine)
    return counts


def _pushed_copy(repo: Path, branch: Branch, config: RepoConfig, refs: Refs) -> Branch:
    """A branch tracking a local one, counted against its copy on the remote Push sends it to
    (`<remote>/<branch>`) once there is one; as it is (no upstream) before."""
    remote = _push_remote(config, branch.name)
    copy = f"{remote}/{branch.name}"
    if remote is None or f"refs/remotes/{copy}" not in refs.names:
        return branch
    ahead, behind = (
        Git(repo)
        .run(
            "rev-list",
            "--left-right",
            "--count",
            f"refs/heads/{branch.name}...refs/remotes/{copy}",
        )
        .split()
    )
    return branch.model_copy(update={"upstream": copy, "ahead": int(ahead), "behind": int(behind)})


def read_worktrees(repo: Path, *, timeout: float | None = None) -> list[tuple[str, str | None]]:
    """`git worktree list`'s worktrees: each one's path and the branch checked out there."""
    git = Git(repo) if timeout is None else Git(repo, timeout=timeout)
    return parse_worktrees(printable(git.run("worktree", "list", "--porcelain", strip=False)))


def common_dir(repo: Path, cache: GitCache) -> Path | None:
    """The common git directory, if Git can identify it promptly."""
    try:
        return git_common_dir(Git(repo, cache=cache, timeout=METADATA_TIMEOUT_SECONDS))
    except GitError, NoAnswerError, OSError:
        return None


@dataclass(frozen=True)
class _Checkout:
    """A configured checkout's Git directories, or the failure to read them."""

    path: Path
    common: Path | None = None
    own: Path | None = None
    error: Exception | None = None

    @classmethod
    def read(cls, path: Path, cache: GitCache) -> _Checkout:
        try:
            git = Git(path, cache=cache)
            return cls(path, common=git_common_dir(git), own=git_dir(git))
        except (GitError, NoAnswerError, OSError) as e:
            return cls(path, error=e)


def _folded(checkouts: list[_Checkout]) -> list[_Checkout]:
    """Keep one path per repository, preferring its main checkout, and every failed lookup."""
    by_common: dict[tuple[bool, Path], _Checkout] = {}
    for checkout in checkouts:
        key = (checkout.error is not None, checkout.common or checkout.path)
        if key not in by_common or checkout.own == checkout.common:
            by_common[key] = checkout
    return list(by_common.values())


def _repos_of(
    checkouts: list[_Checkout], subjects: frozenset[str], cache: GitCache
) -> set[str] | None:
    """The common git directories `subjects` name directly or by branch id. Unknown
    subjects ask for a full collect."""
    known = {str(c.common) for c in checkouts if c.common is not None}
    found: set[str] = set()
    for subject in subjects:
        repo_dir, branch = branch_parts(subject)
        if branch and (common := common_dir(Path(repo_dir), cache)) is not None:
            subject = str(common)
        if subject not in known:
            return None
        found.add(subject)
    return found


def _members(repo: Path) -> set[Path]:
    """Git's worktree paths, including checkouts hidden by the dashboard's globs."""
    try:
        return {
            Path(path).resolve()
            for path, _ in read_worktrees(repo, timeout=METADATA_TIMEOUT_SECONDS)
        }
    except GitError, NoAnswerError, OSError:
        return set()


def collect(
    repos: list[Path],
    now: datetime,
    commit_history_days: int,
    ignored_worktree_globs: list[str],
    subjects: frozenset[str] | None = None,
    previous: LocalSnapshot | None = None,
    github_hosts: Sequence[str] = GITHUB_HOSTS,
) -> LocalSnapshot:
    """Every repo among `repos`, or the ones `subjects` name, keeping the rest as `previous`
    has them. An unknown subject asks for every repo, and a repo `previous` lacks is read
    anyway. When Git can't identify a configured checkout, a healthy configured worktree
    of the same repository can refresh its previous row. Other failures are logged and
    the previous row is kept or left out, so they don't fail the rest; when none can be
    read, the first error is raised."""
    existing = [repo for repo in repos if repo.exists()]
    before = {r.dir: r for r in previous.repos} if previous else {}
    cache = GitCache()
    with ThreadPoolExecutor(max_workers=max(1, len(existing))) as pool:
        checkouts = list(pool.map(_Checkout.read, existing, repeat(cache)))
        only = None if subjects is None else _repos_of(checkouts, subjects, cache)
        folded = _folded(checkouts)
        failed_paths = {
            c.path.resolve() for c in folded if c.error is not None and str(c.path) in before
        }

        def one(checkout: _Checkout) -> tuple[LocalRepo | Exception, set[Path] | None]:
            repo = checkout.path
            if checkout.error is not None:
                return checkout.error, None
            if only is not None and str(repo) in before and str(checkout.common) not in only:
                return before[str(repo)], None
            try:
                listed = read_worktrees(repo) if failed_paths else None
                result = collect_repo(
                    repo,
                    now,
                    commit_history_days,
                    ignored_worktree_globs,
                    github_hosts,
                    listed=listed,
                )
                return result, {Path(path).resolve() for path, _ in listed or []}
            except (GitError, NoAnswerError, OSError) as e:
                return e, None

        results = list(pool.map(one, folded))
        found: dict[int, LocalRepo] = {}
        by_worktree: dict[Path, int] = {}
        cached: list[int] = []
        for index, (result, members) in enumerate(results):
            if isinstance(result, Exception):
                continue
            found[index] = result
            if not failed_paths:
                continue
            if members is None:
                cached.append(index)
                members = {Path(wt.path).resolve() for wt in result.worktrees}
            by_worktree.update((path, index) for path in failed_paths & members)
        unmatched = failed_paths - by_worktree.keys()
        if unmatched:
            # Only cached rows can still hide a worktree we haven't seen in Git's listing.
            paths = (folded[i].path for i in cached)
            for index, members in zip(cached, pool.map(_members, paths), strict=True):
                by_worktree.update((path, index) for path in unmatched & members)
    failed: list[Exception] = []
    for index, (checkout, (result, _)) in enumerate(zip(folded, results, strict=True)):
        if isinstance(result, LocalRepo):
            continue
        failed.append(result)
        previous_repo = before.get(str(checkout.path))
        alternate = by_worktree.get(checkout.path.resolve())
        if previous_repo is not None and alternate is not None and alternate in found:
            # Keep the prior ID and the earliest position of the paired checkouts.
            found[min(index, alternate)] = found.pop(alternate).model_copy(
                update={"dir": previous_repo.dir}
            )
            log.warning(
                "can't read %s directly, so using another worktree: %s", checkout.path, result
            )
            continue
        kept = "keeping what was read before" if previous_repo is not None else "leaving it out"
        log.warning("can't read %s, so %s: %s", checkout.path, kept, result)
        if previous_repo is not None:
            found[index] = previous_repo
    if failed and len(failed) == len(folded):
        raise failed[0]
    return LocalSnapshot(repos=[found[i] for i in sorted(found)])
