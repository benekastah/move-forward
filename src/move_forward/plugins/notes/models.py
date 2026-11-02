"""The notes plugin's data, as its tables hold it."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from ...domain import Model

Size = Literal["quick", "big"]
CheckIn = Literal["checked", "asked_done"]
"""The skill's check-ins on an entry: it checked in on it, or asked whether it's done."""
CHECK_IN_TEXTS: dict[str, CheckIn] = {"Checked": "checked", "Asked-done": "asked_done"}
"""The texts the skill wrote these as, `Declined <date>: Checked`, before they were fields."""


class Link(Model):
    """Something an entry or todo is about: a PR by URL, a ticket by key."""

    kind: str
    """A graph kind, e.g. "pr" or "ticket"."""
    ref: str
    """The entity's id in that kind."""


class Todo(Model):
    id: int
    text: str
    size: Size | None = None
    done_on: date | None = None
    """None while it's open."""
    links: list[Link] = Field(default_factory=list)


class Condition(Model):
    """Something that must happen before a PR merges."""

    pr: str
    """The PR it holds, by URL."""
    text: str


class Declined(Model):
    """Something offered and turned down for the day, e.g. "don't nudge today"."""

    on: date
    text: str


class Entry(Model):
    """A piece of work in the notes: what it is, where, and what's left to do."""

    id: int
    title: str
    status: str | None = None
    where: str | None = None
    """Where the work lives, as written, e.g. "engineering <PR URL>"."""
    blocked_on: str | None = None
    updated_on: date | None = None
    checked_on: date | None = None
    """When the skill last checked in on it."""
    asked_done_on: date | None = None
    """When the skill last asked whether it's done."""
    todos: list[Todo] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    """What must happen before its PRs merge, each about one PR."""
    declined: list[Declined] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    """Lines the notes don't model, kept as written."""
    links: list[Link] = Field(default_factory=list)

    @property
    def open_todos(self) -> list[Todo]:
        return [t for t in self.todos if t.done_on is None]


OfferKind = Literal["nudge", "fresh-review"]
"""What the skill offers about a PR: a nudge to its reviewers (once a day), or a fresh
review (once it changed since the reviewer's last review)."""


class Offer(Model):
    """The last time the skill offered something about a PR."""

    pr: str
    """The PR's URL."""
    kind: OfferKind
    offered_at: datetime


class IcedItem(Model):
    """One thing an act of icing covers: today, a branch."""

    kind: str
    """A graph kind; "branch" for now."""
    ref_id: str
    """The entity's id, e.g. `<repo dir>\\t<branch>`: whatever its publisher made it, never
    parsed."""
    name: str
    """What to call it, e.g. a branch's name, also once its publisher has forgotten it."""
    repo_dir: str | None = None
    """For a branch, its checkout."""
    sha: str | None = None
    """For a branch, its commit when iced, to restore it from."""


class Ice(Model):
    """One act of icing: a stack of branches (or, later, an entry, ticket or PR)."""

    id: int
    title: str
    """e.g. the stack's bottom branch."""
    iced_on: date
    note: str | None = None
    items: list[IcedItem]
    """In order: a stack's bottom branch first."""


class Section(Model):
    """A part of `work-notes.md` that isn't a piece of work, kept as written: the text
    before the first entry, or a section like "Reviews requested of me"."""

    heading: str | None
    """None for the text before the first `##` heading."""
    body: str
