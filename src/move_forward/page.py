"""What the page shows: panels of items with links and action buttons.

Core and every plugin build these; `render` turns them into HTML.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from .domain import Model
from .graph import Ref

PrState = Literal["open", "merged", "closed"]


class Link(Model):
    text: str
    url: str
    strong: bool = False
    """Highlighted, e.g. the PR a todo is about among its entry's PRs."""
    pr_state: PrState | None = None
    """For a PR link: its state if the GitHub snapshot has the PR, else None."""


class Action(Model):
    """A button that posts `fields` to `/actions/<name>`, or, with `href`, a link to a page."""

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
    href: str | None = None
    """Makes the button a link to this page instead of a form (`name` and `fields` unused).
    Links sit at the top right of their item."""
    title: str | None = None
    """A tooltip, e.g. why a link is muted."""
    muted: bool = False
    """Drawn faintly: it can be done, but the data says not yet."""


Effort = Literal["minutes", "hour", "day", "unknown"]


class Signals(Model):
    """What core's Act now ranks an item by, in terms common to every tool."""

    effort: Effort = "unknown"
    """How long doing it takes."""
    closes: str | None = None
    """What doing it finishes, e.g. "APP-12" or "PR 330"; None if it only moves things on."""
    waiting: list[str] = Field(default_factory=list[str])
    """Who is waiting on me for it: logins, names, "customer"."""
    blocked: bool = False
    """Not doable now: waiting on someone else, on CI, or for a decision."""
    importance: int = Field(default=0, ge=0, le=2)
    """0 normal, 1 high, 2 critical: how much it matters beyond its size. The plugin maps its
    tool's idea of it (an escalation, a priority) onto these."""
    waiting_since: datetime | None = None
    """Since when `waiting` have waited (a review requested, a question unanswered): the
    longer, the higher it ranks."""
    due: datetime | None = None
    """A deadline, or when it goes stale: it ranks higher as that nears, most once past."""
    verb: str = ""
    """What to do, as a short imperative: "Reply to 2 threads", "Merge", "Review"."""


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
    subjects: list[Ref] = Field(default_factory=list)
    """The entities the item is about, the main one first, e.g. a PR and its branch; other
    plugins add their buttons by them."""
    signals: Signals | None = None
    """What Act now ranks it by, should its plugin nominate it; None takes its panel's."""
    fingerprint: str | None = None
    """What would make it worth another look once snoozed or dismissed in Act now, e.g. a
    PR's head and review count; None hashes its title, badges and details."""
    parts: list["Item"] = Field(default_factory=list)
    """Pieces focus mode shows one at a time instead of this item, e.g. a work item's todos.

    The dashboard shows this item as is, so the parts should also appear in `details`."""

    def subject(self, kind: str) -> Ref | None:
        """The item's subject of `kind`, e.g. "pr", if it has one."""
        return next((r for r in self.subjects if r.kind == kind), None)


class Panel(Model):
    key: str
    title: str
    description: str
    items: list[Item]
    actions: list[Action] = Field(default_factory=list)
    """Panel-wide buttons, e.g. "delete all"."""
    priority: int = 100
    """Where it goes by default, lowest first: the skill's step order, e.g. 0 for the todos
    the skill starts its check-in with, 10 for reviews asked of me, 130 for what to pick up
    next. The page's layout, once someone has dragged things about, overrides it."""
    act_now: bool = False
    """Its items are candidates for core's Act now (see `Plugin.act_now`): work that's often
    the next thing to do."""
    signals: Signals | None = None
    """The signals of its items that carry none, for Act now: e.g. "Review", in minutes."""


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


def age(now: datetime, then: datetime | None) -> str:
    """How long ago `then` was, as "5m", "3h" or "2d"; "?" when unknown."""
    if then is None:
        return "?"
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    seconds = (now - then).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"
