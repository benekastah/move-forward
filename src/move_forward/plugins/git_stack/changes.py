"""Telling cheaply that git stack's stacks may have moved, for `GitStackPlugin.changed`.

Asked right after each refresh of git, which it watches. Two things move a stack: its
branches (a commit, a rebase, a push or fetch), which git's `git.branch` entities show, so
comparing the graph with the one at the last check says so; and git stack's own record of
each branch's parent, base and group, which it keeps in the repository's `.git/config`
(`branch.<name>.stack*`), so tracking, untracking or grouping changes only that file, which no
entity shows.
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


def branches(graph: Graph, repo_dirs: list[str]) -> list[str]:
    """Where git's branches in `repo_dirs` stand: what moves a stack, in a form to compare."""
    return sorted(
        json.dumps([b.repo_dir, b.name, b.sha, b.ahead, b.behind, b.gone])
        for b in graph.view(BRANCH, _BranchView)
        if b.repo_dir in repo_dirs
    )


def configs(repo_dirs: list[str]) -> str:
    """A digest of the time, size and identity of each of `repo_dirs`' config file, where git
    stack keeps its record."""
    found: list[str] = []
    for repo in sorted(repo_dirs):
        common = git_common_dir(Path(repo))  # a linked worktree's is the main repository's
        path = common / "config" if common else None
        try:
            stat = path.stat() if path else None
        except OSError:
            stat = None
        found.append(json.dumps([repo, stat and [stat.st_ino, stat.st_mtime_ns, stat.st_size]]))
    return hashlib.sha256("\n".join(found).encode()).hexdigest()
