"""The entity kinds github publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...domain import Model


class PrAttrs(Model):
    """A `pr`."""

    repo: str
    """`owner/name`."""
    number: int
    head: str
    base: str
    author: str | None
    draft: bool
    mine: bool
    """I opened it."""
    changes_requested: bool
    created_at: str
    """ISO datetime."""
    merged_at: str | None
    requested_users: list[str] = Field(default_factory=list[str])
    requested_teams: list[str] = Field(default_factory=list[str])
    """A team's top-level name where `[github.teams]` maps it, else its GitHub slug."""
    review_blockers: list[str] | None = None
    """On my open PRs only: why reviewers shouldn't be nudged yet; empty when it's in order."""
    last_review_activity: str | None = None
    """On my open PRs only: the latest review request, review or human comment."""
    review_stale: bool | None = None
    """On my open PRs only: no review activity for `[github] stale_review_hours`."""


class PersonAttrs(Model):
    """A `person`: me, or anyone github knows an email for."""

    me: bool
    email: str | None = None
