"""`[github]`."""

from pydantic import Field

from ...strict import Strict

DEFAULT_HISTORY_DAYS = 14
DEFAULT_REVIEW_CHECKS: list[str] = []
DEFAULT_PLACEHOLDER_LABELS: list[str] = []
DEFAULT_STALE_REVIEW_HOURS = 24
DEFAULT_REVIEWER_HISTORY_DAYS = 180


class GitHubConfig(Strict):
    """`[github]`: the organization and its pull-request conventions."""

    refresh_seconds: int = Field(default=300, ge=10)
    """How often the dashboard refreshes this plugin's data in the background (GitHub's API is rate-limited)."""

    org: str
    """The GitHub organization, as in github.com/<org>."""
    history_days: int = DEFAULT_HISTORY_DAYS
    """How far back to fetch my merged and closed PRs (Jira transitions, cleanup, PR states)."""
    review_checks: list[str] = Field(default_factory=lambda: list(DEFAULT_REVIEW_CHECKS))
    """Names of checks that report review status rather than CI (e.g. a "review-policy" check
    that fails until reviews are in). They never count as CI failures."""
    required_owner_marker: str = ""
    """A comment marker on a CODEOWNERS line whose owners' approval is required, e.g.
    `#required`. Such files count first in team PR scores. Empty: no such convention."""
    ticket_link_prefix: str = ""
    """The word a PR description line starts with to link its ticket, e.g. `Link`. PR
    housekeeping flags PRs with a ticket key but no such line. Empty: no such convention."""
    placeholder_labels: list[str] = Field(default_factory=lambda: list(DEFAULT_PLACEHOLDER_LABELS))
    """PR labels the template leaves to be replaced, e.g. `needs-team`; PR housekeeping
    flags them."""
    teams: dict[str, str] = Field(default_factory=dict)
    """A team's slug, by its name in the top-level `teams`, where the slug isn't the name."""
    email_domains: list[str] = Field(default_factory=list)
    """Domains of work emails, to pick a reviewer's email from their commits. Default: the
    domain of the top-level `email`; set this when the organisation uses several."""
    stale_review_hours: int = DEFAULT_STALE_REVIEW_HOURS
    """A review request with no review activity for this long is nudgeable."""
    reviewer_history_days: int = DEFAULT_REVIEWER_HISTORY_DAYS
    """How far back commits count when suggesting reviewers."""

    def slug(self, team: str) -> str:
        return self.teams.get(team, team)
