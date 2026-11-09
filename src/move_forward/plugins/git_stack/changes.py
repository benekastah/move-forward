"""Telling cheaply that git stack's stacks may have moved, for `GitStackPlugin.changed`.

Asked right after each refresh of git, which it watches. Two things move a stack: its
branches (a commit, a rebase, a push or fetch), which git's `git.branch` entities show; and
git stack's own record of each branch's parent, base and group, which it keeps in the
repository's `.git/config` (`branch.<name>.stack*`), so tracking, untracking or grouping
changes only that file.
"""

import hashlib
import json
from pathlib import Path

from ...api import Graph, Model
from ...api.shell import git_common_dir

BRANCH = "git.branch"


class _BranchView(Model):
    """What this reads of git's `branch`."""

    repo_dir: str
    name: str
    sha: str | None = None
    ahead: int = 0
    behind: int = 0
    gone: bool = False


def fingerprint(graph: Graph, repo_dirs: list[str]) -> str:
    """A digest of what moves a stack in `repo_dirs`: git's branches there, and the time,
    size and identity of each repository's config file."""
    branches = sorted(
        json.dumps([b.repo_dir, b.name, b.sha, b.ahead, b.behind, b.gone])
        for b in graph.view(BRANCH, _BranchView)
        if b.repo_dir in repo_dirs
    )
    configs: list[str] = []
    for repo in sorted(repo_dirs):
        common = git_common_dir(Path(repo))  # a linked worktree's is the main repository's
        path = common / "config" if common else None
        try:
            stat = path.stat() if path else None
        except OSError:
            stat = None
        configs.append(json.dumps([repo, stat and [stat.st_ino, stat.st_mtime_ns, stat.st_size]]))
    return hashlib.sha256("\n".join(branches + configs).encode()).hexdigest()
