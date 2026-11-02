"""Telling cheaply that git-stack's stacks may have moved, for `GitStackPlugin.changed`.

Asked right after each refresh of git, which it watches. Two things move a stack: its
branches (a commit, a rebase, a push or fetch), which git's `branch` entities show; and
git-stack's own record of each branch's parent, base and group, which it keeps in the
repository's `.git/config` (`branch.<name>.stack*`), so tracking, untracking or grouping
changes only that file.
"""

import hashlib
import json
from pathlib import Path

from ...domain import Model
from ...graph import Graph

BRANCH = "branch"


class _BranchView(Model):
    """What this reads of git's `branch`."""

    repo_dir: str
    name: str
    sha: str | None = None
    ahead: int = 0
    behind: int = 0
    gone: bool = False


def config_file(repo: Path) -> Path | None:
    """The file git keeps `repo`'s config in: `.git/config`, or for a linked worktree (whose
    `.git` names its own git directory) the main repository's. None when it isn't a
    checkout."""
    dot = repo / ".git"
    if dot.is_dir():
        return dot / "config"
    if not dot.is_file():
        return None
    text = dot.read_text().strip()
    if not text.startswith("gitdir:"):
        return None
    own = (repo / text.removeprefix("gitdir:").strip()).resolve()
    common = own / "commondir"
    base = (own / common.read_text().strip()).resolve() if common.is_file() else own
    return base / "config"


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
        path = config_file(Path(repo))
        try:
            stat = path.stat() if path else None
        except OSError:
            stat = None
        configs.append(json.dumps([repo, stat and [stat.st_ino, stat.st_mtime_ns, stat.st_size]]))
    return hashlib.sha256("\n".join(branches + configs).encode()).hexdigest()
