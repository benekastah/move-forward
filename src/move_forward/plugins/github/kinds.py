"""The entity kinds github publishes, with their attributes; see `docs/entities.md`."""

from ...api.kinds import Person, PullRequest


class GitHubPullRequest(PullRequest):
    """A `pull_request` (the pull request family), with what only github has."""

    number: int
    """Its number in its repo; `handle` is `#<number>`."""
    base: str
    author: str | None
    review_decision: str | None = None
    """On my open PRs only: GitHub's, e.g. `APPROVED`, `CHANGES_REQUESTED`."""
    rollup_state: str | None = None
    """On my open PRs only: its checks' combined state, e.g. `SUCCESS`, `FAILURE`."""
    mergeable: str | None = None
    """On my open PRs only: `MERGEABLE`, `CONFLICTING` or `UNKNOWN` (while GitHub works it
    out)."""
    in_merge_queue: bool | None = None
    """On my open PRs only."""
    failed_checks: list[str] | None = None
    """On my open PRs only: the checks whose state counts as failed (`FAILURE`, `ERROR`, ...)."""


class GitHubPerson(Person):
    """A `person`: me, or anyone github knows an email for; `handle` is their login."""

    me: bool
