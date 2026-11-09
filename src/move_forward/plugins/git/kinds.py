"""The entity kinds git publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...api import KindModel


class RepoAttrs(KindModel):
    """A `git.repo`: a checkout on GitHub, by `owner/name`."""

    dir: str
    """The main checkout's path."""
    default_branch: str
    default_refs: list[str] = Field(default_factory=list[str])
    """The default branch on each remote (`upstream/main`), or the local one when no remote
    has it: what a commit not yet on the default branch isn't on; empty when there's none.
    Pass them to `git log` in `dir`."""
    my_dirs: list[str] = Field(default_factory=list[str])
    """Directories I committed to within `commit_history_days`, repo-relative and POSIX
    (`src/api`)."""
    commit_history_days: int
    """How far back commits count, in days (`[git] commit_history_days`): for `my_dirs`, and
    for whoever reads others' commits in `dir`."""


class BranchAttrs(KindModel):
    """A `git.branch`: a local branch, by `<repo dir>\\t<name>`."""

    repo_dir: str
    name: str
    sha: str | None
    """The commit it points at, as of the last refresh."""
    ahead: int
    behind: int
    gone: bool
    """Its upstream was deleted."""
    upstream: str | None = None
    """What it tracks, e.g. `origin/me/fix`; None for a branch never pushed. A branch renamed
    since it was pushed still tracks the old name."""
    checked_out_in: str | None
