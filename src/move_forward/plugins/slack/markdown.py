"""What the slack plugin reads from the move-forward skill's markdown, once, with `mfd slack
import`: people's Slack ids and teams' channels from `reviewer-map.md`, and the nudges the
skill logged in `work-notes.md`."""

import re
from datetime import UTC, datetime

from .models import Contact, Ping, TeamChannel

REVIEWER_MAP_FILE = "reviewer-map.md"
WORK_NOTES_FILE = "work-notes.md"
LOGGED_TARGET = "unknown (from work notes)"
"""The target of a nudge the skill logged; it didn't say whom."""

_PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
_NUDGED_RE = re.compile(r"^- Nudged (\d{4}-\d{2}-\d{2})[^:]*:(.*)$", re.MULTILINE)
_SLACK_ID_RE = re.compile(r"\(([CGDU][A-Z0-9]{6,})\)")
_CHANNEL_RE = re.compile(r"#[\w-]+")


def _tables(text: str) -> dict[str, list[list[str]]]:
    """Each `##` section's first markdown table, as body rows of cells (header and divider
    skipped), by heading."""
    sections: dict[str, list[str]] = {}
    heading: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            sections[heading] = []
        elif heading is not None and line.strip().startswith("|"):
            sections[heading].append(line)
    return {
        name: [
            [cell.strip().strip("`").strip() for cell in row.strip().strip("|").split("|")]
            for row in rows[2:]
        ]
        for name, rows in sections.items()
    }


def _cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def read_reviewer_map(text: str) -> tuple[list[Contact], list[TeamChannel]]:
    """People's Slack ids (the Users table: login, email, Slack id) and teams' channels (the
    Teams table: team, channel as `#name (C0123456)`)."""
    tables = _tables(text)
    contacts = [
        Contact(login=login, slack_id=_cell(row, 2) or None)
        for row in tables.get("Users", [])
        if (login := _cell(row, 0))
    ]
    teams: list[TeamChannel] = []
    for row in tables.get("Teams", []):
        if not (slug := _cell(row, 0)):
            continue
        channel = _cell(row, 1)
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
