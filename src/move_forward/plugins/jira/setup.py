"""`mfd init`'s questions for `[jira]`."""

from ...asking import Asker, InitSetup, https_url

TEMPLATE = """\
# refresh_seconds = 900                 # how often it refreshes (tickets change slowly)
# token_env = "JIRA_API_TOKEN"            # else the system's secret store, for your email
# keychain_service = "move-forward-jira"
# status_in_progress = ["In Progress"]    # workflow names the transitions panel uses; a ticket matches any, a move goes to the first it can
# status_in_review = ["In Review"]        # e.g. ["In Review", "Code Review"]
# status_closed = ["Closed"]
# status_blocked = ["Blocked"]           # tickets in it count as blocked, with or without blocker links
# blocked_by_links = []                  # more link names meaning "blocked by", e.g. ["depends on"]
# escalation_label_prefix = ""            # labels that rank incidents, e.g. "escalated-to-"
# escalation_levels = []                  # what follows it, lowest first, e.g. ["manager", "director", "vp"]
# incident_types = ["incident"]           # issue types (substrings, lower-case)
# bug_types = ["bug"]
# [jira.teams]                            # a team's id in Jira's team field (unused yet)
# core = "12345"
"""
EXAMPLE = '# site = "https://example.atlassian.net"\n'


def _required(answer: str) -> str:
    answer = https_url(answer)
    if not answer:
        raise ValueError("the site is required")
    return answer


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    return {"site": ask("Jira site URL (e.g. https://acme.atlassian.net)", "", _required)}
