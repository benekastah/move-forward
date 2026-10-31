"""Parsers for the parts of the move-forward skill's notes that live in core's tables until
slack has its own: reviewer-map.md, and the nudges logged in work-notes.md.

`mfd notes import` reads them; the notes plugin reads the rest of the notes itself.
"""

import re
from datetime import UTC, datetime

from .domain import Ping, ReviewerUser, TeamChannel

REVIEWER_MAP_FILE = "reviewer-map.md"

PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
_NUDGED_RE = re.compile(r"^- Nudged (\d{4}-\d{2}-\d{2})[^:]*:(.*)$", re.MULTILINE)
_SLACK_ID_RE = re.compile(r"\(([CGDU][A-Z0-9]{6,})\)")
_CHANNEL_RE = re.compile(r"#[\w-]+")


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split markdown into (`##` heading, body lines) pairs."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def parse_nudges(text: str) -> list[Ping]:
    """Pings recorded by the skill as `- Nudged YYYY-MM-DD ...: <PR URLs>` lines."""
    pings: list[Ping] = []
    for match in _NUDGED_RE.finditer(text):
        sent_at = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
        for url in PR_URL_RE.findall(match.group(2)):
            pings.append(
                Ping(pr_url=url, target="unknown (from work notes)", kind="first", sent_at=sent_at)
            )
    return pings


def _table_rows(lines: list[str]) -> list[list[str]]:
    """Body rows of the first markdown table in `lines` (header and divider skipped)."""
    rows = [line for line in lines if line.strip().startswith("|")]
    return [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows[2:]]


def _cell(row: list[str], index: int) -> str:
    return row[index].strip().strip("`").strip() if index < len(row) else ""


def parse_reviewer_map(text: str) -> tuple[list[ReviewerUser], list[TeamChannel]]:
    users: list[ReviewerUser] = []
    teams: list[TeamChannel] = []
    for heading, lines in _sections(text):
        if heading == "Users":
            for row in _table_rows(lines):
                if login := _cell(row, 0):
                    users.append(
                        ReviewerUser(
                            login=login,
                            email=_cell(row, 1) or None,
                            slack_id=_cell(row, 2) or None,
                        )
                    )
        elif heading == "Teams":
            for row in _table_rows(lines):
                channel = _cell(row, 1)
                id_match = _SLACK_ID_RE.search(channel)
                name_match = _CHANNEL_RE.search(channel)
                if slug := _cell(row, 0):
                    teams.append(
                        TeamChannel(
                            slug=slug,
                            channel_name=name_match.group(0) if name_match else None,
                            channel_id=id_match.group(1) if id_match else None,
                        )
                    )
    return users, teams
