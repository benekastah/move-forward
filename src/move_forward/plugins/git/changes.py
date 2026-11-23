"""Telling cheaply that a repository moved, for `GitPlugin.changed`, in the repos it can't
watch (see `watch`): a commit, a checkout, a branch made or deleted, a rebase, a push or a
fetch, or a change to the config (an upstream set, a branch git stack tracks). They all
rewrite files git keeps its refs or config in. Git identifies the common directory, then
the times, sizes and identities of those files say what moved.

Edits not yet committed aren't seen: the plugin's `git status` every `status_seconds` catches
those. The index is left out, because editors rewrite it whenever they ask git for the
status."""

import hashlib
import json
from pathlib import Path

from move_forward.api.git import GitCache
from move_forward.plugins.git.client import common_dir


def _ref_files(common: Path) -> list[Path]:
    """The files whose changes mean the repository moved: HEAD, each worktree's HEAD, the
    packed refs, every loose ref (branches, remote branches, tags, the stash), and the config
    (where branches' upstreams are set, and git stack keeps its stacks)."""
    return [
        common / "HEAD",
        common / "packed-refs",
        common / "config",
        *(common / "worktrees").glob("*/HEAD"),
        *(path for path in (common / "refs").rglob("*") if path.is_file()),
    ]


def fingerprints(
    repos: list[Path], *, cache: GitCache | None = None, watched: set[str] | None = None
) -> dict[str, str]:
    """For each repository among `repos` (once, however many of its worktrees are listed),
    by its common git directory: a digest of its ref files that changes when any of them
    does. The watcher reports changes for any common directories listed in `watched`."""
    found: dict[str, str] = {}
    cache = cache if cache is not None else GitCache()
    commons = {common for repo in repos if (common := common_dir(repo, cache)) is not None}
    for common in sorted(commons):
        if watched is not None and str(common) in watched:
            continue
        digest = hashlib.sha256()
        for path in sorted(_ref_files(common)):
            try:
                stat = path.stat()
            except OSError:  # gone since it was listed, or never there (packed-refs)
                continue
            digest.update(f"{path}\0{stat.st_mtime_ns}\0{stat.st_size}\0{stat.st_ino}\n".encode())
        found[str(common)] = digest.hexdigest()
    return found


def fingerprint(repos: list[Path]) -> str:
    """One digest for all of `repos` (see `fingerprints`)."""
    return hashlib.sha256(json.dumps(fingerprints(repos), sort_keys=True).encode()).hexdigest()


def moved(before: dict[str, str], now: dict[str, str]) -> set[str]:
    """The repositories (by common git directory) whose refs moved, or that are new."""
    return {common for common, digest in now.items() if before.get(common) != digest}
