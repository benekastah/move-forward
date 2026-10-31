"""Dashboard configuration, read from a TOML file."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

DEFAULT_CONFIG_PATH = Path("~/.config/move-forward-dashboard/config.toml").expanduser()
DEFAULT_DB_PATH = Path("~/.local/share/move-forward-dashboard/dashboard.db")
DEFAULT_PORT = 8765
DEFAULT_REFRESH_SECONDS = 300
DEFAULT_REFRESH_ON_LOAD_SECONDS = 60
DEFAULT_HISTORY_DAYS = 14
DEFAULT_COMMIT_HISTORY_DAYS = 180
DEFAULT_STALE_REVIEW_HOURS = 24
DEFAULT_IGNORED_WORKTREE_GLOBS = ["*/.claude/worktrees/*"]
DEFAULT_REVIEW_CHECKS: list[str] = []
DEFAULT_JIRA_TOKEN_ENV = "JIRA_API_TOKEN"
DEFAULT_JIRA_KEYCHAIN_SERVICE = "move-forward-dashboard-jira"
DEFAULT_PING_FIRST = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
DEFAULT_PING_FOLLOW_UP = "Gentle bump on this review request"
DEFAULT_PLACEHOLDER_LABELS: list[str] = []
DEFAULT_PRUNE_DAYS = 90
DEFAULT_STATUS_IN_PROGRESS = "In Progress"
DEFAULT_STATUS_IN_REVIEW = "In Review"
DEFAULT_STATUS_CLOSED = "Closed"
DEFAULT_INCIDENT_TYPES = ["incident"]
DEFAULT_BUG_TYPES = ["bug"]


class _Strict(BaseModel):
    """Config models reject unknown keys, so a typo in the file is an error, not silence."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class JiraConfig(_Strict):
    """Jira Cloud access. The API token comes from `token_env`, else the macOS keychain."""

    site: str
    """e.g. https://example.atlassian.net"""
    email: str
    token_env: str = DEFAULT_JIRA_TOKEN_ENV
    keychain_service: str = DEFAULT_JIRA_KEYCHAIN_SERVICE
    status_in_progress: str = DEFAULT_STATUS_IN_PROGRESS
    """Workflow status names, for the transitions the Jira panel proposes."""
    status_in_review: str = DEFAULT_STATUS_IN_REVIEW
    status_closed: str = DEFAULT_STATUS_CLOSED
    escalation_label_prefix: str = ""
    """Prefix of labels that say how far an issue is escalated, e.g. `escalated-to-`;
    empty means no such labels."""
    escalation_levels: list[str] = []
    """What follows the prefix, lowest first, e.g. `["manager", "director", "vp"]`. A higher
    level ranks an incident or bug higher in "What next"; an unlisted level counts lowest."""
    incident_types: list[str] = DEFAULT_INCIDENT_TYPES
    """Issue types (lower-case substrings) that count as incidents in "What next"."""
    bug_types: list[str] = DEFAULT_BUG_TYPES


class TeamConfig(_Strict):
    """A GitHub team (or review group) I review for.

    Its review requests are shown, files it owns in CODEOWNERS count towards a PR's score,
    and PRs of mine that request it get reviewer suggestions.
    """

    github_slug: str
    slack_channel: str | None = None
    """Where review pings for this team go, e.g. `#team-a`. Overrides the reviewer map."""
    jira_team_id: str | None = None
    """The team's id in Jira's team field; unused for now, kept for ticket creation."""


class Config(_Strict):
    """Everything the collectors and panels need to know about me and my machine."""

    org: str
    """GitHub organization."""
    teams: list[TeamConfig]

    @property
    def team_slugs(self) -> list[str]:
        return [team.github_slug for team in self.teams]

    repos: list[Path]
    """Main checkouts of the repos I work in. Their other worktrees are found from them."""
    notes_dir: Path
    """The directory of work-notes.md, on-ice.md and reviewer-map.md: the move-forward skill's
    notes, which the dashboard reads on every refresh and writes todos and icing to."""
    db_path: Path = DEFAULT_DB_PATH
    port: int = DEFAULT_PORT
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    """How often the background loop refreshes when nobody loads the page."""
    refresh_on_load_seconds: int = DEFAULT_REFRESH_ON_LOAD_SECONDS
    """A page load starts a background refresh if the last one began longer ago than this."""
    slack_workspace_url: str = ""
    """Base URL for Slack links, e.g. https://example.slack.com. Empty means no links."""
    email_domain: str = ""
    """Domain of work emails, used to pick a reviewer's email from their commits."""
    history_days: int = DEFAULT_HISTORY_DAYS
    """How far back to look for merged PRs (Jira transitions, cleanup)."""
    commit_history_days: int = DEFAULT_COMMIT_HISTORY_DAYS
    """How far back commit history counts towards "paths I work on" and reviewer suggestions."""
    stale_review_hours: int = DEFAULT_STALE_REVIEW_HOURS
    """A review request with no review activity for this long is nudgeable."""
    ignored_worktree_globs: list[str] = DEFAULT_IGNORED_WORKTREE_GLOBS
    """Worktrees to leave out, e.g. ones agents create for themselves."""
    review_checks: list[str] = DEFAULT_REVIEW_CHECKS
    """Names of checks that report review status rather than CI (e.g. a "review-policy" check
    that fails until reviews are in). They never count as CI failures."""
    required_owner_marker: str = ""
    """A comment marker on a CODEOWNERS line whose owners' approval is required, e.g.
    `#required`. Such files count first in team PR scores. Empty: no such convention."""
    ticket_link_prefix: str = ""
    """The word a PR description line starts with to link its ticket, e.g. `Link`. PR
    housekeeping flags PRs with a ticket key but no such line. Empty: no such convention."""
    my_branch_prefixes: list[str] = []
    """Branch name prefixes that mark a branch as mine, e.g. `["jane/"]`. When set, branches
    without one are left out of Cleanup and Local work unless they have a PR of mine."""
    ping_first: str = DEFAULT_PING_FIRST
    """The first review reminder; `{url}` is the PR."""
    ping_follow_up: str = DEFAULT_PING_FOLLOW_UP
    placeholder_labels: list[str] = DEFAULT_PLACEHOLDER_LABELS
    """PR labels the template leaves to be replaced, e.g. `needs-team`; PR housekeeping
    flags them."""
    prune_days: int = DEFAULT_PRUNE_DAYS
    """Work finished this long ago (merged PRs, closed tickets, done todos) is cleared from
    the notes, and old pings from the database, by a daily job."""
    jira: JiraConfig | None = None

    @field_validator("repos", "notes_dir", "db_path")
    @classmethod
    def _expand(cls, value: Path | list[Path]) -> Path | list[Path]:
        if isinstance(value, list):
            return [p.expanduser() for p in value]
        return value.expanduser()


def load(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load the config file."""
    with open(path, "rb") as f:
        return Config.model_validate(tomllib.load(f))
