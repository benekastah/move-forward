"""Parsers for the move-forward skill's notes: work-notes.md, on-ice.md and reviewer-map.md.

The skill owns these files; the dashboard only reads them and imports them into SQLite.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from .domain import (
    IcedBranch,
    IcedStack,
    Notes,
    Ping,
    ReviewerUser,
    TeamChannel,
    Todo,
    WorkItem,
)

WORK_NOTES_FILE = "work-notes.md"
ON_ICE_FILE = "on-ice.md"
REVIEWER_MAP_FILE = "reviewer-map.md"

REVIEWS_SECTION = "Reviews requested of me"
"""A work-notes section that logs offered reviews and nudges, not a piece of work."""

PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_FIELD_RE = re.compile(r"^- ([^:]+?):\s*(.*)$")
_CHILD_RE = re.compile(r"^\s+- (.*)$")
_TRAILING_DATE_RE = re.compile(r"\s+\d{4}-\d{2}-\d{2}.*$")
_SIZE_RE = re.compile(r"^\[(quick|big)\]\s*", re.IGNORECASE)
_NUDGED_RE = re.compile(r"^- Nudged (\d{4}-\d{2}-\d{2})[^:]*:(.*)$", re.MULTILINE)
_SLACK_ID_RE = re.compile(r"\(([CGDU][A-Z0-9]{6,})\)")
_CHANNEL_RE = re.compile(r"#[\w-]+")
_ICE_HEADING_RE = re.compile(r"^(\S+)\s+\((.+)\)$")


class _Field:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.children: list[str] = []


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split markdown into (`##` heading, body lines) pairs."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return sections


def _fields(lines: list[str]) -> dict[str, _Field]:
    """Parse `- Key: value` lines and their indented children.

    Keys lose a trailing date, so `Declined 2026-10-29` becomes `Declined`; repeated keys
    append to the same field.
    """
    fields: dict[str, _Field] = {}
    current: _Field | None = None
    for line in lines:
        if match := _FIELD_RE.match(line):
            key = _TRAILING_DATE_RE.sub("", match.group(1)).strip()
            current = fields.setdefault(key, _Field())
            if match.group(2):
                current.values.append(match.group(2))
        elif (match := _CHILD_RE.match(line)) and current is not None:
            current.children.append(match.group(1))
    return fields


def _first(fields: dict[str, _Field], key: str) -> str | None:
    field = fields.get(key)
    return field.values[0] if field and field.values else None


def _todo(text: str) -> Todo:
    match = _SIZE_RE.match(text)
    if match:
        size = match.group(1).lower()
        return Todo(text=text[match.end() :], size="quick" if size == "quick" else "big")
    return Todo(text=text, size=None)


def parse_work_notes(text: str) -> list[WorkItem]:
    items: list[WorkItem] = []
    for title, lines in _sections(text):
        if title == REVIEWS_SECTION:
            continue
        fields = _fields(lines)
        todos = [_todo(child) for child in fields.get("Left to do", _Field()).children]
        items.append(
            WorkItem(
                title=title,
                fields={
                    key: field.values + field.children
                    for key, field in fields.items()
                    if key != "Left to do"
                },
                todos=todos,
                text="\n".join([title, *lines]),
            )
        )
    return items


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


def parse_on_ice(text: str) -> list[IcedStack]:
    stacks: list[IcedStack] = []
    for heading, lines in _sections(text):
        match = _ICE_HEADING_RE.match(heading)
        if not match:
            continue
        fields = _fields(lines)
        branches: list[IcedBranch] = []
        for child in fields.get("Branches", _Field()).children:
            name, _, sha = child.partition(" ")
            branches.append(IcedBranch(name=name, sha=sha.strip()))
        stacks.append(
            IcedStack(
                bottom_branch=match.group(1),
                repo_dir=match.group(2),
                iced=_first(fields, "Iced"),
                note=_first(fields, "Note"),
                branches=branches,
            )
        )
    return stacks


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


def load(notes_dir: Path) -> Notes:
    """Read and parse all three files. A missing file counts as empty."""

    def read(name: str) -> str:
        path = notes_dir / name
        return path.read_text() if path.exists() else ""

    work_notes = read(WORK_NOTES_FILE)
    users, teams = parse_reviewer_map(read(REVIEWER_MAP_FILE))
    return Notes(
        work_items=parse_work_notes(work_notes),
        pings=parse_nudges(work_notes),
        iced_stacks=parse_on_ice(read(ON_ICE_FILE)),
        users=users,
        teams=teams,
    )
