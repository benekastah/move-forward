"""What the page shows: panels of items with links and action buttons.

Core and every plugin build these; `render` turns them into HTML.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .domain import Model

PrState = Literal["open", "merged", "closed"]


class Link(Model):
    text: str
    url: str
    strong: bool = False
    """Highlighted, e.g. the PR a todo is about among its entry's PRs."""
    pr_state: PrState | None = None
    """For a PR link: its state if the GitHub snapshot has the PR, else None."""


class Action(Model):
    """A button that posts `fields` to `/actions/<name>`."""

    name: str
    label: str
    fields: dict[str, str]
    confirm: str | None = None
    """A question to confirm before running, for destructive actions."""
    checkbox: bool = False
    """Render as a checkbox that submits when toggled, instead of a button."""
    checked: bool = False
    suggested: bool = False
    """Drawn bigger: the data says this is probably the thing to do."""
    input: str | None = None
    """Placeholder of a text field, posted as `input`, that the button submits along."""


class Item(Model):
    title: str
    url: str | None = None
    subtitle: str = ""
    badges: list[str] = Field(default_factory=list)
    """Short tags, e.g. `draft`, `conflicts`, `+120`."""
    details: list[str] = Field(default_factory=list)
    """Longer lines shown under the title."""
    links: list[Link] = Field(default_factory=list)
    rank: float = 0
    """Higher sorts first within a panel."""
    draft: bool = False
    """A draft PR; drafts sort after everything else whatever their rank."""
    actions: list[Action] = Field(default_factory=list)
    parts: list["Item"] = Field(default_factory=list)
    """Pieces focus mode shows one at a time instead of this item, e.g. a work item's todos.

    The dashboard shows this item as is, so the parts should also appear in `details`."""


class Panel(Model):
    key: str
    title: str
    description: str
    items: list[Item]
    actions: list[Action] = Field(default_factory=list)
    """Panel-wide buttons, e.g. "delete all"."""


class SourceStatus(Model):
    fetched_at: datetime | None = None
    error: str | None = None


class Group(Model):
    """A collapsible section of the page holding related panels."""

    key: str
    title: str
    panel_keys: list[str]
    open: bool = False
    """Expanded when the page loads; the rest start collapsed."""
