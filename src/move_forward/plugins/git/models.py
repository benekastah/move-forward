"""The git plugin's data: checkouts, their worktrees and branches."""

from datetime import datetime

from pydantic import Field

from ...domain import Model


class Worktree(Model):
    path: str
    branch: str | None = None
    """None for a detached HEAD."""
    modified: int
    """Changed tracked files, staged or not."""
    untracked: int


class Branch(Model):
    name: str
    sha: str | None = None
    """The commit it points at."""
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    gone: bool = False
    """The upstream was deleted."""
    committed_at: datetime
    ahead_of_default: int | None = None
    """Commits not on the default branch; only computed for recent branches with no upstream."""
    my_commits: int | None = None
    """My commits on it that aren't on the default branch, anywhere in its history, not just
    its tip; only counted for recent branches, None for the rest."""


class LocalRepo(Model):
    dir: str
    github_repo: str | None = None
    """owner/name of the main remote, if it is on GitHub, else of another that is (see
    `client.read_remotes`)."""
    default_branch: str
    default_refs: list[str] = Field(default_factory=list)
    """The default branch on each remote (`origin/main`, `upstream/main`), or the local one
    when no remote has it: what "not on the default branch yet" is measured against."""
    worktrees: list[Worktree]
    branches: list[Branch]
    my_dirs: list[str] = Field(default_factory=list)
    """Directories I committed to within `commit_history_days`."""


class LocalSnapshot(Model):
    repos: list[LocalRepo]
