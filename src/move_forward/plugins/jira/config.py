"""`[jira]`."""

from urllib.parse import urlsplit

from pydantic import Field, field_validator

from ...strict import Strict

DEFAULT_REFRESH_SECONDS = 900
DEFAULT_JIRA_TOKEN_ENV = "JIRA_API_TOKEN"
DEFAULT_JIRA_KEYCHAIN_SERVICE = "move-forward-jira"
DEFAULT_STATUS_IN_PROGRESS = "In Progress"
DEFAULT_STATUS_IN_REVIEW = "In Review"
DEFAULT_STATUS_CLOSED = "Closed"
DEFAULT_STATUS_BLOCKED = "Blocked"
DEFAULT_STATUS_ROLLOUT = "Rollout"
DEFAULT_PRIORITIES = ["Lowest", "Low", "Medium", "High", "Highest"]
DEFAULT_INCIDENT_TYPES = ["incident"]
DEFAULT_BUG_TYPES = ["bug"]


class JiraConfig(Strict):
    """`[jira]`: Jira Cloud access and the workflow's names. Optional.

    Requests authenticate as the top-level `email` with the API token from `token_env`,
    else the system's secret store.
    """

    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=10)
    """How often the dashboard refreshes this plugin's data in the background (tickets change slowly)."""

    site: str
    """e.g. https://example.atlassian.net. Must be `https://`: requests carry the API token."""
    token_env: str = DEFAULT_JIRA_TOKEN_ENV
    """The environment variable holding the API token."""
    keychain_service: str = DEFAULT_JIRA_KEYCHAIN_SERVICE
    """The service the token is stored under in the system's secret store (the macOS
    keychain, Windows' Credential Manager, the desktop keyring), when `token_env` isn't set;
    its account is the top-level `email`."""
    status_in_progress: list[str] = Field(default_factory=lambda: [DEFAULT_STATUS_IN_PROGRESS])
    """Workflow status names for "in progress", e.g. `["In Progress", "Doing"]`, for the
    transitions the Jira panel proposes. A ticket is in one if its status is any of the names
    (without regard to case); a move goes to the first name the ticket can move to. The
    same goes for the other `status_*`."""
    status_in_review: list[str] = Field(default_factory=lambda: [DEFAULT_STATUS_IN_REVIEW])
    """Names for "in review", e.g. `["In Review", "Code Review"]`."""
    status_closed: list[str] = Field(default_factory=lambda: [DEFAULT_STATUS_CLOSED])
    """Names for closed, e.g. `["Closed", "Done"]`."""
    status_blocked: list[str] = Field(default_factory=lambda: [DEFAULT_STATUS_BLOCKED])
    """A ticket in one of these statuses counts as blocked in "What next" and Act now, even
    with no blocker links, e.g. `["Blocked", "On Hold"]`."""
    status_rollout: list[str] = Field(default_factory=lambda: [DEFAULT_STATUS_ROLLOUT])
    """Names for a rollout, e.g. `["Rollout"]`: the code is merged and the ticket waits on
    monitoring. Such a ticket is never proposed for closing when its PRs merge; once they
    all have, one low-urgency Act now item asks to finish the rollout. It is never moved
    into, and "What next" ranks it below unblocked work. Empty: no such status."""
    priorities: list[str] = Field(default_factory=lambda: list(DEFAULT_PRIORITIES))
    """Jira's priority names, lowest first, for `priority_raised` notifications: a priority
    not listed here is never counted as raised."""
    blocked_by_links: list[str] = Field(default_factory=list)
    """Names of links, as the blocked ticket sees them, that also mean "blocked by", e.g.
    `["depends on"]`. "is blocked by" always does."""
    escalation_label_prefix: str = ""
    """Prefix of labels that say how far an issue is escalated, e.g. `escalated-to-`;
    empty means no such labels."""
    escalation_levels: list[str] = Field(default_factory=list)
    """What follows the prefix, lowest first, e.g. `["manager", "director", "vp"]`. A higher
    level ranks an incident or bug higher in "What next"; an unlisted level counts lowest."""
    incident_types: list[str] = Field(default_factory=lambda: list(DEFAULT_INCIDENT_TYPES))
    """Issue types (lower-case substrings) that count as incidents in "What next"."""
    bug_types: list[str] = Field(default_factory=lambda: list(DEFAULT_BUG_TYPES))
    """Issue types (lower-case substrings) that count as bugs in "What next"."""
    teams: dict[str, str] = Field(default_factory=dict)
    """A team's id in Jira's team field, by its name in the top-level `teams`; unused for
    now, kept for ticket creation."""

    @field_validator("site")
    @classmethod
    def _https(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme != "https" or not url.netloc:
            raise ValueError(
                f"{value!r} must be an https:// URL, since requests carry the API token"
            )
        return value
