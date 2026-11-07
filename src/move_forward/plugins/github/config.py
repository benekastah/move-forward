"""`[github]`."""

from typing import Literal

from pydantic import Field

from ...api import Strict

DEFAULT_REFRESH_SECONDS = 300
DEFAULT_HOST = "github.com"
DEFAULT_HISTORY_DAYS = 14
DEFAULT_POLICY_CHECKS: list[str] = []
DEFAULT_PLACEHOLDER_LABELS: list[str] = []
DEFAULT_STALE_REVIEW_HOURS = 24
DEFAULT_STALE_DRAFT_DAYS = 14
DEFAULT_MERGE_QUEUE_EVICTION_DAYS = 2
DEFAULT_QUICK_REVIEW_MAX_LINES = 100
DEFAULT_QUICK_REVIEW_MAX_FILES = 5
DEFAULT_HOUR_REVIEW_MAX_LINES = 300
DEFAULT_HOUR_REVIEW_MAX_FILES = 12
DEFAULT_SPLIT_PR_SUGGEST_LINES = 600
DEFAULT_SPLIT_PR_LINES = 800
DEFAULT_REVIEWER_HISTORY_DAYS = 180
ReviewerAssignment = Literal["team", "individual"]


class GitHubConfig(Strict):
    """`[github]`: the organization and its pull-request conventions."""

    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=10)
    """How often the dashboard refreshes this plugin's data in the background (GitHub's API is rate-limited)."""

    host: str = DEFAULT_HOST
    """GitHub's host: github.com, or a GitHub Enterprise Server's (e.g. `github.acme.com`),
    which `gh` must be logged in to (`gh auth login --hostname <host>`)."""
    org: str
    """The GitHub organization, as in https://<host>/<org>."""
    search_scope: Literal["org", "everywhere"] = "org"
    """Where my PR searches look: `org` (default) only in `org`'s repositories, `everywhere`
    in every repository on the host that I author, review or am asked to review in."""
    history_days: int = DEFAULT_HISTORY_DAYS
    """How far back to fetch my merged and closed PRs (Jira transitions, cleanup, PR states)."""
    policy_checks: list[str] = Field(default_factory=lambda: list(DEFAULT_POLICY_CHECKS))
    """Names of checks that enforce PR policy rather than test the code (e.g. a check that
    fails until the PR has a risk label, or enough assignees), e.g. `["pr-policy"]`. A
    failing one is a PR policy item, not a CI failure."""
    ignored_reviewers: list[str] = Field(default_factory=list)
    """Logins whose comments and reviews aren't review feedback, such as a policy bot that
    comments "update the risk label", e.g. `["policy-bot"]`. They're left out of feedback to
    address, review activity, nudge blockers and notifications; other bots' (review tools')
    comments count as feedback."""
    required_owner_marker: str = ""
    """A comment marker on a CODEOWNERS line whose owners' approval is required, e.g.
    `#!required`. Such files count first in team PR scores. Empty: no such convention."""
    ticket_link_prefixes: list[str] = Field(default_factory=list)
    """The words a PR description line can start with to link its ticket, e.g.
    `["Link", "Closes"]`; the ticket follows as a key or a Jira URL. PR housekeeping flags
    PRs with a ticket key but no such line. Empty: no such convention."""
    placeholder_labels: list[str] = Field(default_factory=lambda: list(DEFAULT_PLACEHOLDER_LABELS))
    """PR labels the template leaves to be replaced, e.g. `needs-team`; PR housekeeping
    flags them."""
    teams: dict[str, str] = Field(default_factory=dict)
    """A team's slug, by its name in the top-level `teams`, where the slug isn't the name."""
    default_team_reviewer_assignment: ReviewerAssignment = "team"
    """Whether a requested team alone is enough (`team`), or each requested team also
    needs a requested member (`individual`). A PR with no reviewer request still needs one."""
    team_reviewer_assignment: dict[str, ReviewerAssignment] = Field(default_factory=dict)
    """Overrides by GitHub team slug, e.g. `{bacon = "individual"}`. A person may satisfy
    several teams when they belong to each of them."""
    primary_reviewer_repos: list[str] = Field(default_factory=list)
    """Repos (`owner/name`) where a PR has a primary reviewer: an assignee besides its author,
    who must approve, e.g. `["acme/widgets"]`. My PRs there get a field to pick one. GitHub
    enforces the approval; the dashboard doesn't check it."""
    email_domains: list[str] = Field(default_factory=list)
    """Domains of work emails, to pick a reviewer's email from their commits. Default: the
    domain of the top-level `email`; set this when the organisation uses several."""
    stale_review_hours: int = DEFAULT_STALE_REVIEW_HOURS
    """A review request with no review activity for this long is nudgeable."""
    quick_review_max_lines: int = Field(default=DEFAULT_QUICK_REVIEW_MAX_LINES, ge=0)
    """A review takes minutes when the PR changes at most this many lines (added plus
    deleted), e.g. `100`, and at most `quick_review_max_files` files. Otherwise it takes an
    hour within the `hour_review_max_*` limits, and a day beyond them. The limits grow
    slowly on purpose: a PR's complexity grows faster than its size."""
    quick_review_max_files: int = Field(default=DEFAULT_QUICK_REVIEW_MAX_FILES, ge=0)
    """The most files a quick (minutes) review can change, e.g. `5`."""
    hour_review_max_lines: int = Field(default=DEFAULT_HOUR_REVIEW_MAX_LINES, ge=0)
    """The most lines an hour's review can change, e.g. `300`."""
    hour_review_max_files: int = Field(default=DEFAULT_HOUR_REVIEW_MAX_FILES, ge=0)
    """The most files an hour's review can change, e.g. `12`."""
    split_pr_suggest_lines: int = Field(default=DEFAULT_SPLIT_PR_SUGGEST_LINES, ge=0)
    """My open PRs changing at least this many lines (added plus deleted), e.g. `600`, get a
    "may need splitting" line in PR housekeeping."""
    split_pr_lines: int = Field(default=DEFAULT_SPLIT_PR_LINES, ge=0)
    """My open PRs changing at least this many lines, e.g. `800`, are "worth splitting"."""
    split_pr_labels: list[str] = Field(default_factory=list)
    """Labels that mark a PR as worth splitting whatever its lines, e.g.
    `["size/XL", "size/XXL"]`."""
    merge_queue_eviction_days: int = Field(default=DEFAULT_MERGE_QUEUE_EVICTION_DAYS, ge=0)
    """How long a removal from the merge queue stays in CI failures, e.g. `2` days, unless
    the PR is back in the queue, was queued again, or its checks pass now."""
    stale_draft_days: int = Field(default=DEFAULT_STALE_DRAFT_DAYS, ge=0)
    """My draft PRs untouched for this many days, e.g. `14`, get a "stale draft" line in PR
    housekeeping. 0: never."""
    reviewer_history_days: int = DEFAULT_REVIEWER_HISTORY_DAYS
    """How far back commits count when suggesting reviewers."""

    def slug(self, team: str) -> str:
        return self.teams.get(team, team)

    def has_primary_reviewer(self, repo: str) -> bool:
        """Whether PRs in `repo` (`owner/name`) have a primary reviewer."""
        return repo.casefold() in {r.casefold() for r in self.primary_reviewer_repos}
