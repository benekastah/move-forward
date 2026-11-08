"""The families of entity kinds: what more than one tool may represent, core's (a pull
request, a ticket, a person, an organisation) and those plugins declare (`Plugin.families`). A plugin's kind
named for a family must extend its model,
adding the plugin's own fields, so every publisher's entities of the family carry what the
family's model says and other plugins read them through it. A plugin's other kinds are its
own, named for it (`git.branch`). See `docs/entities.md`."""

import re
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import Field

from .domain import KindModel

PULL_REQUEST = "pull_request"
"""The family of pull requests (and merge requests): shown as "PR"."""
TICKET = "ticket"
"""The family of tickets (issues), which link what's about them."""
PERSON = "person"
"""The family of people."""
ORGANISATION = "organisation"
"""The family of organisations: a CRM's account, a support tool's organisation, a company in
an address book. A person points at theirs."""
TAG = "tag"
"""Core's own kind: a `#tag`, carried by the entities whose title or mentions have it, or that I put it on
(see `tags`); text naming it only points at it. No plugin publishes it, nor declares a
family by its name."""

REVIEW = "review"
"""The task (`Signals.task`) of reviewing a pull request, whoever publishes it: `mfd snooze`
snoozes it."""

_OWN_KIND = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")


def named_for(plugin: str, kind: str) -> bool:
    """Whether `kind` is named for `plugin`, as a plugin's own kinds are: `git.branch`."""
    return _OWN_KIND.fullmatch(kind) is not None and kind.startswith(f"{plugin}.")


class PullRequest(KindModel):
    """What every pull request has, whoever publishes it."""

    handle: str
    """How its tool shows it, e.g. `#7`: for display, not identity (that's its URL)."""
    repo: str
    """`owner/name`."""
    head: str
    """The branch it merges."""
    head_oid: str | None = None
    """The commit its head branch is at in its tool, as of the last refresh."""
    draft: bool
    mine: bool
    """I opened it."""
    changes_requested: bool
    created_at: str
    """ISO datetime."""
    merged_at: str | None
    requested_users: list[str] = Field(default_factory=list[str])
    requested_teams: list[str] = Field(default_factory=list[str])
    """A team's top-level name where the plugin maps it, else the tool's own name for it."""
    review_blockers: list[str] | None = None
    """On my open PRs only: why reviewers shouldn't be nudged yet; empty when it's in order.
    `already approved` says it has the approval it needs, so there's nobody to chase."""
    last_review_activity: str | None = None
    """On my open PRs only: the latest review request, review or human comment."""
    failing_policy_checks: list[str] | None = None
    """On my open PRs only: the failing checks that report review policy."""
    stale_review_hours: int | None = None
    """On my open PRs only: how long review may be quiet before it's nudgeable."""
    progress: str | None = None
    """On my open PRs only: `draft`, `ready to merge`, `CI failing` or `in review`."""
    review_stale: bool | None = None
    """On my open PRs only: quiet for `stale_review_hours`."""


class Ticket(KindModel):
    """What every ticket has, whoever publishes it."""

    handle: str
    """How its tool shows it, e.g. `APP-12`: also what text names it by."""
    summary: str


class Person(KindModel):
    """What every person has, whoever publishes them."""

    handle: str
    """Their name in the tool, e.g. a login."""
    email: str | None = None
    """What joins them to the same person in other tools (`Graph.same_person`)."""
    contact_type: str | None = None
    """What they are to me, when the tool knows: "customer", say, from a CRM. See `people`."""
    importance: int | None = Field(default=None, ge=0, le=2)
    """How much they matter to me, when the tool knows (0 normal, 1 high, 2 critical): a CRM's
    VIP, a contact I starred. It raises the items about them as a contact type's does."""


class Organisation(KindModel):
    """What every organisation has, whoever publishes it. A `person` points at the
    organisations they're at; what they are to me counts for its people (see `people`)."""

    handle: str
    """Its name in the tool."""
    contact_type: str | None = None
    """What it is to me, when the tool knows: "customer", say, from a CRM."""
    importance: int | None = Field(default=None, ge=0, le=2)
    """How much it matters to me, when the tool knows (0 normal, 1 high, 2 critical): a key
    account. Its people's items rank by it as by a contact type."""


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_SHARED_MAILBOX = re.compile(r"(no-?reply|do-?not-?reply|notifications?)(\+.*)?")
"""The local parts of addresses many people's mail or commits come from, e.g.
`noreply@github.com`: sharing one says nothing about who someone is."""


def personal_email(text: str | None) -> str | None:
    """`text` lower-cased, if it's an address that identifies one person; None otherwise."""
    email = (text or "").strip().lower()
    if not _EMAIL.fullmatch(email) or _SHARED_MAILBOX.fullmatch(email.split("@")[0]):
        return None
    return email


def is_url(id: str) -> bool:
    """Whether `id` is an absolute http(s) URL: a PR's or a ticket's id."""
    parts = urlsplit(id)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


_HOST_LOGIN = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]+)?/[^/\s]+")


def is_host_login(id: str) -> bool:
    """Whether `id` is `<host>/<login>`, e.g. `github.com/alice`: a person's id, and an
    organisation's (`<host>/<its id there>`)."""
    return _HOST_LOGIN.fullmatch(id) is not None


class PullRequestFetched(KindModel):
    """What a pull request fetched on demand (`Graph.fetch`) has, whoever publishes it."""

    closed_at: str | None
    """When it was merged or closed, an ISO datetime; null while it's open."""


class TicketFetched(KindModel):
    """What a ticket fetched on demand (`Graph.fetch`) has, whoever publishes it."""

    resolved_at: str | None
    """When it was resolved, an ISO datetime; null while it's open."""


@dataclass(frozen=True)
class Family:
    name: str
    label: str
    """What the page and the CLI call it, e.g. "PR"."""
    model: type[KindModel]
    id_rule: Callable[[str], bool]
    """Whether an id fits the family: ids are unique whoever publishes them, so two tools'
    `OPS-12` (or two people's `alice`) are never taken for one."""
    fetched: type[KindModel] | None = None
    """What its entities fetched on demand carry (`KindSpec.fetched_attrs`), when they can
    be."""


CORE_FAMILIES = (
    Family(PULL_REQUEST, "PR", PullRequest, is_url, PullRequestFetched),
    Family(TICKET, "ticket", Ticket, is_url, TicketFetched),
    Family(PERSON, "person", Person, is_host_login),
    Family(ORGANISATION, "organisation", Organisation, is_host_login),
)
"""The families core declares itself, as a plugin declares its own (`Plugin.families`)."""
FAMILIES: dict[str, Family] = {}
"""Every family, by name: core's and the plugins' (`declare`, as plugins are found)."""
_DECLARED_BY: dict[str, str] = {}
_FAMILY_NAME = re.compile(r"[a-z][a-z0-9_]*")


def declare(plugin: str, families: Iterable[Family]) -> None:
    """Add a plugin's families to `FAMILIES`, or raise ValueError, adding none, for one whose
    name isn't a family's (lower-case, no dot: a dotted kind is a plugin's own), whose model
    has no `handle` (what text names its entities by), or that core or another plugin declares
    differently. The same declaration from two plugins (a family package they share) is one
    family."""
    found: dict[str, Family] = {}
    for family in families:
        if family.name == TAG:
            raise ValueError(f"{plugin}'s family {TAG} is core's kind")
        if not _FAMILY_NAME.fullmatch(family.name):
            raise ValueError(
                f"{plugin}'s family {family.name!r} isn't named like one: {_FAMILY_NAME.pattern}"
            )
        if "handle" not in family.model.model_fields:
            raise ValueError(f"{plugin}'s family {family.name}'s model has no handle")
        known = found.get(family.name) or FAMILIES.get(family.name)
        if known is not None and known != family:
            owner = plugin if family.name in found else _DECLARED_BY[family.name]
            raise ValueError(f"{plugin}'s family {family.name} is declared differently by {owner}")
        found[family.name] = family
    for name, family in found.items():
        if name not in FAMILIES:
            _DECLARED_BY[name] = plugin
        FAMILIES[name] = family


@contextmanager
def declaring(plugin: str, families: Iterable[Family], *, keep: bool = True) -> Generator[None]:
    """Declare a plugin's families (`declare`) for the block: undone if the block raises (the
    plugin is left out), and when it ends unless `keep`."""
    saved, declared_by = dict(FAMILIES), dict(_DECLARED_BY)
    kept = False
    try:
        declare(plugin, families)
        yield
        kept = keep
    finally:
        if not kept:
            FAMILIES.clear()
            FAMILIES.update(saved)
            _DECLARED_BY.clear()
            _DECLARED_BY.update(declared_by)


declare("core", CORE_FAMILIES)


def fits(kind: str, id: str) -> bool:
    """Whether `id` fits its family's rule; any id does for a plugin's own kind."""
    family = FAMILIES.get(kind)
    return family is None or family.id_rule(id)


def label(kind: str) -> str:
    """What the page and the CLI call a kind: its family's label ("PR"), else its name."""
    family = FAMILIES.get(kind)
    return family.label if family else kind
