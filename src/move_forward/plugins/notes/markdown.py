"""The move-forward skill's markdown notes, `work-notes.md` and `on-ice.md`, read into the
notes plugin's models and written back from them.

Reading keeps what it doesn't understand: an entry's unknown lines stay with the entry, and
sections that aren't pieces of work stay as written, so writing the notes back loses nothing
but the order of an entry's lines.
"""

import re
from datetime import date
from pathlib import Path

from .models import (
    CHECK_IN_TEXTS,
    CheckIn,
    Condition,
    Declined,
    Entry,
    Ice,
    IcedItem,
    Link,
    Section,
    Size,
    Todo,
)

WORK_NOTES_FILE = "work-notes.md"
ON_ICE_FILE = "on-ice.md"
REVIEWS_SECTION = "Reviews requested of me"
"""A work-notes section that logs offered reviews and nudges, not a piece of work."""
ON_ICE_TITLE = "# Stacks on ice"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""
PR = "pr"
BRANCH = "branch"

PR_URL_RE = re.compile(r"https://[\w.-]+(?::\d+)?/[\w.-]+/[\w.-]+/pull/\d+")
"""A pull request's URL, on github.com or a GitHub Enterprise Server."""
_FIELD_RE = re.compile(r"^- ([^:]+?):\s*(.*)$")
_DATED_RE = re.compile(r"^(Done|Declined) (\d{4}-\d{2}-\d{2})$")
_CHILD_RE = re.compile(r"^\s+- (.*)$")
_SIZE_RE = re.compile(r"^\[(quick|big)\]\s*", re.IGNORECASE)
_ICE_HEADING_RE = re.compile(r"^(\S+)\s+\((.+)\)$")
_LAST_ASKED_RE = re.compile(r"^Last asked:\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)


def links_in(text: str) -> list[Link]:
    """The PRs `text` names by URL, each once. The tickets it names aren't links: the graph
    finds them (see `Graph.named`)."""
    return [Link(kind=PR, ref=url) for url in dict.fromkeys(PR_URL_RE.findall(text))]


CONDITION_LINE = "- Merge condition: "
"""How a condition that holds no PR is kept: as a line of its entry, as written."""


def attach_conditions(texts: list[str], entry_prs: list[str]) -> tuple[list[Condition], list[str]]:
    """Conditions written for a whole entry (as the skill writes them, and as the notes kept
    them before each named its PR), each given its PR: the one its text names (dropping a
    leading `<url>: `, as `entry_markdown` writes it); else each of the entry's PRs, which
    keeps them all held as before. With no PR at all, it can't hold anything yet, so it's
    kept as a line of the entry instead (`CONDITION_LINE`), to add again with its PR."""
    conditions: list[Condition] = []
    kept: list[str] = []
    for text in texts:
        named = list(dict.fromkeys(PR_URL_RE.findall(text)))
        if len(named) == 1 and text.startswith(f"{named[0]}: "):
            text = text.removeprefix(f"{named[0]}: ")
        if prs := named or entry_prs:
            conditions += [Condition(pr=pr, text=text) for pr in prs]
        else:
            kept.append(CONDITION_LINE + text)
    return conditions, kept


def _sized(text: str) -> tuple[str, Size | None]:
    if match := _SIZE_RE.match(text):
        return text[match.end() :], "quick" if match.group(1).lower() == "quick" else "big"
    return text, None


def _todo(text: str, done_on: date | None = None) -> Todo:
    text, size = _sized(text)
    return Todo(id=0, text=text, size=size, done_on=done_on, links=links_in(text))


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _sections(text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """The text before the first `##` heading, and each (heading, body lines) after it."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble).strip("\n"), sections


def _entry(title: str, lines: list[str]) -> Entry:
    """One `## <title>` section of `work-notes.md`."""
    known: dict[str, str] = {}
    todos: list[Todo] = []
    conditions: list[str] = []
    """As written, for the whole entry; `attach_conditions` gives each its PR."""
    declined: list[Declined] = []
    check_ins: dict[CheckIn, date] = {}
    """The latest of each check-in, which the skill wrote as `Declined <date>: Checked`."""
    kept: list[str] = []
    block: str | None = None
    """The field whose `  - ` children the next lines may be: "todos", "conditions" or "kept"."""
    for line in lines:
        if (child := _CHILD_RE.match(line)) and block:
            if block == "todos":
                todos.append(_todo(child.group(1)))
            elif block == "conditions":
                conditions.append(child.group(1))
            else:
                kept.append(line)
            continue
        block = None
        field = _FIELD_RE.match(line)
        if not field:
            if line.strip():
                kept.append(line)
            continue
        key, value = field.group(1).strip(), field.group(2).strip()
        dated = _DATED_RE.match(key)
        on = date.fromisoformat(dated.group(2)) if dated else None
        if dated and on and dated.group(1) == "Done":
            todos.append(_todo(value, done_on=on))
        elif dated and on and (which := CHECK_IN_TEXTS.get(value)):
            check_ins[which] = max(on, check_ins.get(which, on))
        elif dated and on:
            declined.append(Declined(on=on, text=value))
        elif key == "Left to do" and not value:
            block = "todos"
        elif key == "Merge conditions":
            conditions += [value] if value else []
            block = "conditions"
        elif (
            key == "Updated"
            and "Updated" not in known
            and _date(value)
            or key in ("Status", "Where", "Blocked on")
            and key not in known
        ):
            known[key] = value
        else:
            kept.append(line)
            block = "kept"
    text = "\n".join([title, *lines])
    links = links_in(text)
    attached, unattached = attach_conditions(conditions, [link.ref for link in links])
    return Entry(
        id=0,
        title=title,
        status=known.get("Status"),
        where=known.get("Where"),
        blocked_on=known.get("Blocked on"),
        updated_on=_date(known.get("Updated", "")),
        checked_on=check_ins.get("checked"),
        asked_done_on=check_ins.get("asked_done"),
        todos=todos,
        conditions=attached,
        declined=declined,
        lines=kept + unattached,
        links=links,
    )


def read_work_notes(text: str) -> tuple[list[Entry], list[Section]]:
    """The entries in `work-notes.md`, and its other sections as written."""
    preamble, parts = _sections(text)
    entries: list[Entry] = []
    sections = [Section(heading=None, body=preamble)] if preamble else []
    titles: set[str] = set()
    for title, lines in parts:
        if title == REVIEWS_SECTION or title in titles:
            # Not a piece of work, or a second entry by the same title: kept as written.
            sections.append(Section(heading=title, body="\n".join(lines).strip("\n")))
            continue
        titles.add(title)
        entries.append(_entry(title, lines))
    return entries, sections


def _expanded(repo_dir: str) -> str:
    return str(Path(repo_dir).expanduser())


def _tilde(repo_dir: str) -> str:
    path, home = Path(repo_dir), Path.home()
    # Forward slashes on every system: the skill reads the file, and Path reads them back.
    return "~/" + path.relative_to(home).as_posix() if path.is_relative_to(home) else repo_dir


def read_on_ice(text: str, today: date) -> tuple[list[Ice], date | None]:
    """The stacks in `on-ice.md`, and when the skill last asked about them. A stack without
    an `Iced` date counts as iced `today`."""
    preamble, parts = _sections(text)
    last_asked = _LAST_ASKED_RE.search(preamble)
    ices: list[Ice] = []
    for heading, lines in parts:
        match = _ICE_HEADING_RE.match(heading)
        if not match:
            continue
        bottom, repo_dir = match.group(1), _expanded(match.group(2))
        iced_on, note, items = today, None, list[IcedItem]()
        in_branches = False
        for line in lines:
            if (child := _CHILD_RE.match(line)) and in_branches:
                name, _, sha = child.group(1).partition(" ")
                ref_id = f"{repo_dir}{BRANCH_ID_SEPARATOR}{name}"
                items.append(
                    IcedItem(
                        kind=BRANCH,
                        ref_id=ref_id,
                        name=name,
                        repo_dir=repo_dir,
                        sha=sha.strip() or None,
                    )
                )
                continue
            in_branches = False
            if field := _FIELD_RE.match(line):
                key, value = field.group(1).strip(), field.group(2).strip()
                if key == "Iced":
                    iced_on = _date(value) or today
                elif key == "Note":
                    note = value or None
                elif key == "Branches":
                    in_branches = True
        ices.append(Ice(id=0, title=bottom, iced_on=iced_on, note=note, items=items))
    return ices, date.fromisoformat(last_asked.group(1)) if last_asked else None


def _todo_line(todo: Todo) -> str:
    sized = f"[{todo.size}] {todo.text}" if todo.size else todo.text
    if todo.done_on:
        return f"- Done {todo.done_on.isoformat()}: {sized}"
    return f"  - {sized}"


def entry_markdown(entry: Entry) -> str:
    """An entry as a `## <title>` section of `work-notes.md`, in the skill's format."""
    lines = [f"## {entry.title}"]
    if entry.status is not None:
        lines.append(f"- Status: {entry.status}")
    if entry.where is not None:
        lines.append(f"- Where: {entry.where}")
    open_todos = [t for t in entry.todos if t.done_on is None]
    if open_todos:
        lines.append("- Left to do:")
        lines += [_todo_line(t) for t in open_todos]
    lines += [_todo_line(t) for t in entry.todos if t.done_on is not None]
    if entry.blocked_on is not None:
        lines.append(f"- Blocked on: {entry.blocked_on}")
    if entry.conditions:
        lines.append("- Merge conditions:")
        lines += [f"  - {c.pr}: {c.text}" for c in entry.conditions]
    lines += [f"- Declined {d.on.isoformat()}: {d.text}" for d in entry.declined]
    for text, on in (("Checked", entry.checked_on), ("Asked-done", entry.asked_done_on)):
        if on is not None:  # as the skill wrote them
            lines.append(f"- Declined {on.isoformat()}: {text}")
    lines += entry.lines
    if entry.updated_on is not None:
        lines.append(f"- Updated: {entry.updated_on.isoformat()}")
    return "\n".join(lines)


def write_work_notes(entries: list[Entry], sections: list[Section]) -> str:
    """`work-notes.md` again: the text before the first entry, the entries, then the other
    sections."""
    preamble = next((s.body for s in sections if s.heading is None), "# Work notes")
    parts = [preamble, *(entry_markdown(e) for e in entries)]
    parts += [f"## {s.heading}\n{s.body}".rstrip() for s in sections if s.heading is not None]
    return "\n\n".join(parts) + "\n"


def write_on_ice(ices: list[Ice], last_asked: date | None) -> str:
    """`on-ice.md` again, in the skill's format."""
    parts = [ON_ICE_TITLE]
    if last_asked:
        parts.append(f"Last asked: {last_asked.isoformat()}")
    for ice in ices:
        repo_dir = next((i.repo_dir for i in ice.items if i.repo_dir), "?")
        lines = [f"## {ice.title} ({_tilde(repo_dir)})", f"- Iced: {ice.iced_on.isoformat()}"]
        if ice.note:
            lines.append(f"- Note: {ice.note}")
        lines.append("- Branches:")
        for item in ice.items:
            lines.append(f"  - {item.name} {item.sha or ''}".rstrip())
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"
