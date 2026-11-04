"""What the page shows: panels of items with links and action buttons.

Core and every plugin build these; `render` turns them into HTML.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from .domain import Model
from .graph import Ref

ANCHOR_PREFIX = "item-"


def anchor(kind: str, id: str) -> str:
    """The id of the item about an entity (its main subject) on the page, e.g.
    `item-pull_request-https-github-com-acme-widgets-pull-12`: a link to `/#<id>` opens the
    dashboard at it."""
    return ANCHOR_PREFIX + re.sub(r"[^A-Za-z0-9_-]+", "-", f"{kind}-{id}").strip("-")


class Link(Model):
    text: str
    url: str
    strong: bool = False
    """Highlighted, e.g. the PR a todo is about among its entry's PRs."""
    done: bool = False
    """What it links to is over (a PR merged or closed, a ticket resolved): struck through."""


class Badge(Model):
    """A badge with a tone; a plain string is a `plain` one."""

    text: str
    tone: Literal["plain", "warn", "good"] = "plain"
    """`warn` for what needs attention (conflicts, failing CI, blocked), `good` for what's
    going well."""


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
    """Not doable now: waiting on someone else, on CI, or for a decision. Act now ranks it
    lower, until `touched` is `blocked_stale_days` or more ago."""
    touched: datetime | None = None
    """When anything last happened on it (a ticket's last update or comment, an entry's last
    edit): a blocked item untouched that long is worth another look. None: it never goes
    stale."""
    importance: int = Field(default=0, ge=-1, le=2)
    """-1 not important, 0 normal, 1 high, 2 critical: how much it matters beyond its size.
    The plugin maps its tool's idea of it (an escalation, a priority) onto 0 to 2; -1 and a
    replacement of the rest come from a priority I set (see `Nomination.prioritized`)."""
    waiting_since: datetime | None = None
    """Since when `waiting` have waited (a review requested, a question unanswered): the
    longer, the higher it ranks."""
    due: datetime | None = None
    """A deadline, or when it goes stale: it ranks higher as that nears, most once past."""
    verb: str = ""
    """What to do, as a short imperative: "Reply to 2 threads", "Merge", "Review"."""
    task: str = Field(default="", pattern=r"^([a-z][a-z0-9_]*)?$")
    """What doing it is, as a stable id: `review`, `merge`, `transition`. Act now keeps what I
    said of an item (a snooze, a pin) by its main subject and this, so rewording `verb`, or
    a count in it changing, keeps it. A nominated item must have one."""


class Item(Model):
    title: str
    url: str | None = None
    subtitle: str = ""
    badges: list[str | Badge] = Field(default_factory=list[str | Badge])
    """Short tags, e.g. `+120`, or `Badge("conflicts", "warn")` for one that needs
    attention."""
    details: list[str] = Field(default_factory=list)
    """Longer lines shown under the title."""
    links: list[Link] = Field(default_factory=list)
    rank: float = 0
    """Higher sorts first within a panel."""
    deferred: bool = False
    """Not for now, e.g. a draft PR: it sorts after everything else in its panel, whatever
    its rank, and Act now leaves it out unless its plugin nominates it in `act_now`."""
    actions: list[Action] = Field(default_factory=list)
    subjects: list[Ref] = Field(default_factory=list)
    """The entities the item is about, the main one first, e.g. a PR and its branch; other
    plugins add their buttons by them."""
    signals: Signals | None = None
    """What Act now ranks it by, should its plugin nominate it; None takes its panel's."""
    fingerprint: str | None = None
    """What would make it worth another look once snoozed or dismissed in Act now, e.g. a
    PR's head and review count; None hashes its title, badges and details."""
    act_now_fields: str | None = None
    """Set by core, in Act now: the JSON fields (`kind`, `id`, `task`, `fingerprint`, `also`)
    naming the item for its feedback, which opening it posts (`act_now/opened`)."""
    opened_at: datetime | None = None
    """Set by core, in Act now: when I opened the item, if it hasn't changed since; it's
    shown dimmed."""
    parts: list["Item"] = Field(default_factory=list)
    """Pieces focus mode shows one at a time instead of this item, e.g. a work item's todos.

    The dashboard shows this item as is, so the parts should also appear in `details`."""

    @property
    def anchor(self) -> str | None:
        """The id the page gives the item, from its main subject; None without one."""
        return anchor(self.subjects[0].kind, self.subjects[0].id) if self.subjects else None

    def subject(self, kind: str) -> Ref | None:
        """The item's subject of `kind`, e.g. "pull_request", if it has one."""
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
    snoozable: bool = False
    """Its items, which must be `act_now` ones, get a Snooze button, and stay off the panel
    until they change or the snooze ends: the same snooze as Act now's."""


Surface = Literal["dashboard", "focus", "act_now"]
"""Where an item is shown: the dashboard's panels, focus mode, or Act now."""


class ItemAdditions(Model):
    """What a plugin adds to another plugin's item (`Plugin.item_actions`)."""

    actions: list[Action] = Field(default_factory=list)
    badges: list[str | Badge] = Field(default_factory=list[str | Badge])
    details: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)


class Identity(Model):
    """Who I am in a tool, for the page's footer."""

    tool: str
    """The plugin's title, e.g. "GitHub"."""
    login: str


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
