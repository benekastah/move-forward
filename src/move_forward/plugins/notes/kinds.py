"""The entity kinds notes publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...domain import Model


class EntryAttrs(Model):
    """An `entry`: a piece of work in the notes, by title."""

    merge_conditions: list[str] = Field(default_factory=list[str])
    open_todos: list[str] = Field(default_factory=list[str])


class IcedBranchAttrs(Model):
    """An `iced_branch`: a branch on ice, by `<repo dir>\\t<name>` like git's `branch`."""

    repo_dir: str
    name: str
    stack: str
    """The title of the act of icing it's in: its stack's bottom branch."""
    sha: str | None
    """The commit it pointed at when iced."""
