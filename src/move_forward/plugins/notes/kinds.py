"""The entity kinds notes publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...domain import Model


class EntryAttrs(Model):
    """An `entry`: a piece of work in the notes, by title."""

    merge_conditions: list[str] = Field(default_factory=list[str])
    open_todos: list[str] = Field(default_factory=list[str])


class IcedAttrs(Model):
    """An `iced`: one act of icing, by its id. It hides what it points at: its items, and
    what else it covers, like its branches' PRs."""

    iced_on: str
    """ISO date."""
    note: str | None = None
    shas: dict[str, str] = Field(default_factory=dict[str, str])
    """The commit each branch it put on ice pointed at then, by the branch's id."""
