"""`[jira]`."""

from pydantic import Field

from ...strict import Strict

DEFAULT_JIRA_TOKEN_ENV = "JIRA_API_TOKEN"
DEFAULT_JIRA_KEYCHAIN_SERVICE = "move-forward-jira"
DEFAULT_STATUS_IN_PROGRESS = "In Progress"
DEFAULT_STATUS_IN_REVIEW = "In Review"
DEFAULT_STATUS_CLOSED = "Closed"
DEFAULT_INCIDENT_TYPES = ["incident"]
DEFAULT_BUG_TYPES = ["bug"]


class JiraConfig(Strict):
    """`[jira]`: Jira Cloud access and the workflow's names. Optional.

    Requests authenticate as the top-level `email` with the API token from `token_env`,
    else the macOS keychain.
    """

    site: str
    """e.g. https://example.atlassian.net"""
    token_env: str = DEFAULT_JIRA_TOKEN_ENV
    """The environment variable holding the API token."""
    keychain_service: str = DEFAULT_JIRA_KEYCHAIN_SERVICE
    """The macOS keychain item's service, when `token_env` isn't set; its account is the
    top-level `email`."""
    status_in_progress: str = DEFAULT_STATUS_IN_PROGRESS
    """Workflow status names, for the transitions the Jira panel proposes."""
    status_in_review: str = DEFAULT_STATUS_IN_REVIEW
    status_closed: str = DEFAULT_STATUS_CLOSED
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
