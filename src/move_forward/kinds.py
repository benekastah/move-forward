"""The families of entity kinds core models: what more than one tool may represent (a pull
request, a ticket, a person). A plugin's kind named for a family must extend its model,
adding the plugin's own fields, so every publisher's entities of the family carry what the
family's model says and other plugins read them through it. A plugin's other kinds are its
own, named for it (`git.branch`). See `docs/entities.md`."""

import re
from collections.abc import Callable
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
    """On my open PRs only: why reviewers shouldn't be nudged yet; empty when it's in order."""
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


def is_url(id: str) -> bool:
    """Whether `id` is an absolute http(s) URL: a PR's or a ticket's id."""
    parts = urlsplit(id)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


_HOST_LOGIN = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]+)?/[^/\s]+")


def is_host_login(id: str) -> bool:
    """Whether `id` is `<host>/<login>`, e.g. `github.com/alice`: a person's id."""
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


FAMILIES = {
    family.name: family
    for family in (
        Family(PULL_REQUEST, "PR", PullRequest, is_url, PullRequestFetched),
        Family(TICKET, "ticket", Ticket, is_url, TicketFetched),
        Family(PERSON, "person", Person, is_host_login),
    )
}
"""Every family, by name."""


def fits(kind: str, id: str) -> bool:
    """Whether `id` fits its family's rule; any id does for a plugin's own kind."""
    family = FAMILIES.get(kind)
    return family is None or family.id_rule(id)


def label(kind: str) -> str:
    """What the page and the CLI call a kind: its family's label ("PR"), else its name."""
    family = FAMILIES.get(kind)
    return family.label if family else kind
