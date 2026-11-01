"""What the slack plugin reads from the move-forward skill's markdown, once, with `mfd slack
import`: people's Slack ids and teams' channels from `reviewer-map.md`, and the nudges the
skill logged in `work-notes.md`."""

import re
from datetime import UTC, datetime

from ...markdown import cell, tables
from .models import Contact, Ping, TeamChannel

REVIEWER_MAP_FILE = "reviewer-map.md"
WORK_NOTES_FILE = "work-notes.md"
LOGGED_TARGET = "unknown (from work notes)"
"""The target of a nudge the skill logged; it didn't say whom."""

_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
_NUDGED_RE = re.compile(r"^- Nudged (\d{4}-\d{2}-\d{2})[^:]*:(.*)$", re.MULTILINE)
_SLACK_ID_RE = re.compile(r"\(([CGDU][A-Z0-9]{6,})\)")
_CHANNEL_RE = re.compile(r"#[\w-]+")


def read_reviewer_map(text: str) -> tuple[list[Contact], list[TeamChannel]]:
    """People's Slack ids (the Users table: login, email, Slack id) and teams' channels (the
    Teams table: team, channel as `#name (C0123456)`)."""
    by_heading = tables(text)
    contacts = [
        Contact(login=login, slack_id=cell(row, 2) or None)
        for row in by_heading.get("Users", [])
        if (login := cell(row, 0))
    ]
    teams: list[TeamChannel] = []
    for row in by_heading.get("Teams", []):
        if not (slug := cell(row, 0)):
            continue
        channel = cell(row, 1)
        id_match = _SLACK_ID_RE.search(channel)
        name_match = _CHANNEL_RE.search(channel)
        teams.append(
            TeamChannel(
                slug=slug,
                channel_name=name_match.group(0) if name_match else None,
                channel_id=id_match.group(1) if id_match else None,
            )
        )
    return contacts, teams


def read_nudges(text: str) -> list[Ping]:
    """Pings the skill logged as `- Nudged YYYY-MM-DD ...: <PR URLs>` lines."""
    pings: list[Ping] = []
    for match in _NUDGED_RE.finditer(text):
        sent_at = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
        for url in _PR_URL_RE.findall(match.group(2)):
            pings.append(Ping(pr_url=url, target=LOGGED_TARGET, kind="first", sent_at=sent_at))
    return pings
