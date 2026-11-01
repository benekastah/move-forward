"""The entity kinds git publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...domain import Model


class RepoAttrs(Model):
    """A `repo`: a checkout on GitHub, by `owner/name`."""

    dir: str
    """The main checkout's path."""
    default_branch: str
    my_dirs: list[str] = Field(default_factory=list[str])
    """Directories I committed to within `[git] commit_history_days`."""


class BranchAttrs(Model):
    """A `branch`: a local branch, by `<repo dir>\\t<name>`."""

    repo_dir: str
    name: str
    sha: str | None
    """The commit it points at, as of the last refresh."""
    ahead: int
    behind: int
    gone: bool
    """Its upstream was deleted."""
    checked_out_in: str | None
