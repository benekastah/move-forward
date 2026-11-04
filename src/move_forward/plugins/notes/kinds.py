"""The entity kinds notes publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...api import Model


class EntryAttrs(Model):
    """An `notes.entry`: a piece of work in the notes, by title."""

    merge_conditions: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    """What must happen before each of its PRs merges, by the PR's URL."""
    open_todos: list[str] = Field(default_factory=list[str])


class TodoAttrs(Model):
    """A `notes.todo`: one open todo of a work entry, by its id. Ticking it off takes it away."""

    entry: str
    """Its entry's title."""
    size: str | None = None
    """`quick` or `big`; None while unsized."""


class IcedAttrs(Model):
    """An `notes.iced`: one act of icing, by its id. It hides what it points at: its items, and
    what else it covers, like its branches' PRs."""

    iced_on: str
    """ISO date."""
    note: str | None = None
    shas: dict[str, str] = Field(default_factory=dict[str, str])
    """The commit each branch it put on ice pointed at then, by the branch's id."""
