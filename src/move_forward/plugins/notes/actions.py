"""The notes plugin's buttons: todos, icing and thawing, posted to `/actions/notes/<action>`.

Every action takes the form fields of its button and returns a one-line notice for the page.
A repo must be one the graph has branches of, i.e. one of git's checkouts.
"""

import contextlib
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

from ...domain import IcedStack
from ...graph import Graph
from ...notes import ON_ICE_FILE, WORK_NOTES_FILE, parse_on_ice
from ...plugin import ActionError
from ...shell import CommandError, git
from .config import NotesConfig

ICED_FROM_DASHBOARD_NOTE = "iced from the dashboard"
TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""
BRANCH = "branch"
TICKET = "ticket"


def _repo(graph: Graph, repo_dir: str) -> Path:
    """`repo_dir`, if the graph has branches in it (from `[git] repos`)."""
    prefix = repo_dir + TARGET_SEPARATOR
    if not any(b.ref.id.startswith(prefix) for b in graph.of_kind(BRANCH)):
        raise ActionError(f"{repo_dir} is not a configured repo")
    return Path(repo_dir)


def branch_exists(repo: Path, branch: str) -> bool:
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except CommandError:
        return False
    return True


def checked_out_in(repo: Path, branch: str) -> str | None:
    """The worktree the branch is checked out in, if any."""
    path = git(repo, "for-each-ref", "--format=%(worktreepath)", f"refs/heads/{branch}")
    return path.strip() or None


def _tilde(path: Path) -> str:
    home = Path.home()
    return "~/" + str(path.relative_to(home)) if path.is_relative_to(home) else str(path)


def _stack_branches(graph: Graph, repo: Path, branch: str) -> list[str]:
    """The branch's whole stack, bottom first, from the `stack_parent` git-stack puts on
    branches; just the branch when it isn't in a stack (or git-stack isn't on)."""
    prefix = f"{repo}{TARGET_SEPARATOR}"
    parents = {
        e.ref.id.removeprefix(prefix): parent
        for e in graph.of_kind(BRANCH)
        if e.ref.id.startswith(prefix) and isinstance(parent := e.attrs.get("stack_parent"), str)
    }
    if branch not in parents:
        return [branch]
    bottom, seen = branch, {branch}
    while parents[bottom] in parents and parents[bottom] not in seen:  # up to the root
        bottom = parents[bottom]
        seen.add(bottom)
    children: dict[str, list[str]] = {}
    for name, parent in parents.items():
        children.setdefault(parent, []).append(name)
    stack: list[str] = []
    pending = [bottom]
    while pending:
        name = pending.pop()
        if name not in stack:
            stack.append(name)
            pending += reversed(children.get(name, []))
    return stack


def ice(notes: NotesConfig, graph: Graph, fields: dict[str, str], today: date | None = None) -> str:
    """Append the branch's stack to on-ice.md, as the move-forward skill records it."""
    repo = _repo(graph, fields["repo"])
    branch = fields["branch"]
    if not branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    path = notes.dir / ON_ICE_FILE
    text = path.read_text() if path.exists() else "# Stacks on ice\n"
    branches = _stack_branches(graph, repo, branch)
    if any(re.search(rf"^  - {re.escape(b)} ", text, re.MULTILINE) for b in branches):
        raise ActionError(f"{branch} is already on ice")
    lines = [
        "",
        f"## {branches[0]} ({_tilde(repo)})",
        f"- Iced: {(today or date.today()).isoformat()}",
        f"- Note: {ICED_FROM_DASHBOARD_NOTE}",
        "- Branches:",
    ]
    for name in branches:
        lines.append(f"  - {name} {git(repo, 'rev-parse', name).strip()}")
    path.write_text(text.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
    return f"put {branches[0]} on ice ({len(branches)} branches)"


def _iced_stacks(notes: NotesConfig) -> list[IcedStack]:
    path = notes.dir / ON_ICE_FILE
    return parse_on_ice(path.read_text()) if path.exists() else []


def delete_iced(notes: NotesConfig, graph: Graph, fields: dict[str, str]) -> str:
    """Delete an iced stack's local branches and drop it from on-ice.md.

    Checks every branch first, so a branch checked out in a worktree stops it before anything
    is deleted. Remote branches and PRs are left alone.
    """
    bottom = fields["branch"]
    stack = next((s for s in _iced_stacks(notes) if s.bottom_branch == bottom), None)
    if stack is None:
        raise ActionError(f"{bottom} is not on ice")
    repo = _repo(graph, str(Path(stack.repo_dir).expanduser()))
    names = [b.name for b in stack.branches]
    existing = [name for name in names if branch_exists(repo, name)]
    for name in existing:
        if worktree := checked_out_in(repo, name):
            raise ActionError(f"{name} is checked out in {worktree}; switch it away first")
    if existing:
        # Not tracked by git-stack, or git-stack isn't installed: nothing to untrack.
        with contextlib.suppress(CommandError, FileNotFoundError):
            git(repo, "stack", "untrack", *existing)
        git(repo, "branch", "-D", *existing)
    thaw(notes, graph, {"branch": bottom})
    return f"deleted {len(existing)} branches of {bottom} and took it off ice"


def thaw(notes: NotesConfig, graph: Graph, fields: dict[str, str]) -> str:
    """Remove a stack's entry from on-ice.md."""
    bottom = fields["branch"]
    path = notes.dir / ON_ICE_FILE
    text = path.read_text() if path.exists() else ""
    pattern = re.compile(rf"\n## {re.escape(bottom)} \(.*?\)\n.*?(?=\n## |\Z)", re.DOTALL)
    new_text, count = pattern.subn("", text)
    if not count:
        raise ActionError(f"{bottom} is not on ice")
    path.write_text(new_text.rstrip("\n") + "\n")
    return f"thawed {bottom}"


# Work-note todos. The notes keep open todos as `  - [size] text` lines under `- Left to do:`
# and done ones as `- Done YYYY-MM-DD: text` lines, as the move-forward skill writes them. A
# todo ticked off here keeps its `[size]` in the Done line, so unticking restores it.


def entry_bounds(lines: list[str], entry: str) -> tuple[int, int]:
    """Indexes of an entry's `## ` line and of the line after its last."""
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"## {entry}"), None)
    if start is None:
        raise ActionError(f"no work-notes entry titled {entry}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def left_to_do(lines: list[str], start: int, end: int) -> tuple[int, int] | None:
    """Indexes of the entry's `- Left to do:` line and of the line after its last child."""
    head = next((i for i in range(start, end) if lines[i].startswith("- Left to do")), None)
    if head is None:
        return None
    tail = head + 1
    while tail < end and lines[tail].startswith("  "):
        tail += 1
    return head, tail


def _find_todo(lines: list[str], start: int, end: int, text: str) -> tuple[int, str | None]:
    """The line index of the open todo `text` in the entry, and its size."""
    block = left_to_do(lines, start, end)
    pattern = re.compile(rf"^  - (?:\[(quick|big)\] )?{re.escape(text)}\s*$")
    if block:
        for i in range(block[0] + 1, block[1]):
            if match := pattern.match(lines[i]):
                return i, match.group(1)
    raise ActionError(f"no open todo {text!r} in {lines[start][3:]}")


def _pop_todo(lines: list[str], start: int, end: int, text: str) -> str | None:
    """Remove the open todo `text` from the entry, returning its size."""
    i, size = _find_todo(lines, start, end, text)
    del lines[i]
    return size


def _touch_updated(lines: list[str], start: int, end: int, today: date) -> None:
    updated = f"- Updated: {today.isoformat()}"
    for i in range(start, end):
        if lines[i].startswith("- Updated:"):
            lines[i] = updated
            return
    lines.insert(_entry_text_end(lines, start, end), updated)


def _entry_text_end(lines: list[str], start: int, end: int) -> int:
    """The index after the entry's last non-blank line."""
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _edit_entry(
    notes: NotesConfig, entry: str, edit: Callable[[list[str], int, int], str], today: date | None
) -> str:
    """Apply `edit` to the entry's lines in work-notes.md, bump its Updated date, and save."""
    path = notes.dir / WORK_NOTES_FILE
    if not path.exists():
        raise ActionError(f"{path} doesn't exist")
    lines = path.read_text().split("\n")
    start, end = entry_bounds(lines, entry)
    notice = edit(lines, start, end)
    start, end = entry_bounds(lines, entry)
    _touch_updated(lines, start, end, today or date.today())
    path.write_text("\n".join(lines))
    return notice


def todo_done(
    notes: NotesConfig, graph: Graph, fields: dict[str, str], today: date | None = None
) -> str:
    """Move an open todo to a `Done <today>:` line of its entry."""
    text = fields["text"]

    def edit(lines: list[str], start: int, end: int) -> str:
        size = _pop_todo(lines, start, end, text)
        block = left_to_do(lines, start, end - 1)
        marker = f"[{size}] " if size else ""
        at = block[1] if block else _entry_text_end(lines, start, end - 1)
        lines.insert(at, f"- Done {(today or date.today()).isoformat()}: {marker}{text}")
        return f"done: {text}"

    return _edit_entry(notes, fields["entry"], edit, today)


def todo_undone(
    notes: NotesConfig, graph: Graph, fields: dict[str, str], today: date | None = None
) -> str:
    """Move a todo from its `Done` line back under `Left to do`, at the end."""
    text = fields["text"]
    pattern = re.compile(
        rf"^- Done \d{{4}}-\d{{2}}-\d{{2}}: (\[(?:quick|big)\] )?{re.escape(text)}\s*$"
    )

    def edit(lines: list[str], start: int, end: int) -> str:
        for i in range(start, end):
            if match := pattern.match(lines[i]):
                marker = match.group(1) or ""
                del lines[i]
                break
        else:
            raise ActionError(f"no done todo {text!r} in {fields['entry']}")
        block = left_to_do(lines, start, end - 1)
        if block is None:
            at = _entry_text_end(lines, start, end - 1)
            lines[at:at] = ["- Left to do:", f"  - {marker}{text}"]
        else:
            lines.insert(block[1], f"  - {marker}{text}")
        return f"reopened: {text}"

    return _edit_entry(notes, fields["entry"], edit, today)


def todo_delete(
    notes: NotesConfig, graph: Graph, fields: dict[str, str], today: date | None = None
) -> str:
    """Drop an open todo from its entry without recording it as done."""
    text = fields["text"]

    def edit(lines: list[str], start: int, end: int) -> str:
        _pop_todo(lines, start, end, text)
        return f"dropped: {text}"

    return _edit_entry(notes, fields["entry"], edit, today)


JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def _link_url(graph: Graph, value: str) -> str:
    """A ticket key as its URL (from whichever plugin knows tickets), or a URL as given."""
    if JIRA_KEY_RE.fullmatch(value):
        url = graph.url_for(TICKET, value)
        if url is None:
            raise ActionError("no [jira] site configured to link ticket keys")
        return url
    if value.startswith(("https://", "http://")) and " " not in value:
        return value
    raise ActionError(f"{value!r} is neither a Jira key nor a URL")


def todo_link(
    notes: NotesConfig, graph: Graph, fields: dict[str, str], today: date | None = None
) -> str:
    """Append a Jira ticket's or PR's URL to an open todo's line."""
    text = fields["text"]
    url = _link_url(graph, fields["input"].strip())

    def edit(lines: list[str], start: int, end: int) -> str:
        if url in text:
            raise ActionError(f"{url} is already in that todo")
        i, _ = _find_todo(lines, start, end, text)
        lines[i] = lines[i].rstrip() + " " + url
        return f"linked {url}"

    return _edit_entry(notes, fields["entry"], edit, today)


def _ensure_entry(notes: NotesConfig, entry: str, where: str, today: date) -> None:
    """Append a work-notes entry titled `entry` if there is none, as the skill would."""
    path = notes.dir / WORK_NOTES_FILE
    text = path.read_text() if path.exists() else "# Work notes\n"
    if re.search(rf"^## {re.escape(entry)}\s*$", text, re.MULTILINE):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["", f"## {entry}", "- Status: inferred"]
    if where:
        lines.append(f"- Where: {where}")
    lines += ["- Left to do:", f"- Updated: {today.isoformat()}"]
    path.write_text(text.rstrip("\n") + "\n" + "\n".join(lines) + "\n")


def todo_add(
    notes: NotesConfig, graph: Graph, fields: dict[str, str], today: date | None = None
) -> str:
    """Add an open todo to an entry: `input` (or `text`) with an optional `size`, quick or big.

    With `create`, a missing entry is created first (`where` names its PR or ticket).
    """
    text = (fields.get("input") or fields.get("text") or "").strip()
    if not text:
        raise ActionError("the todo is empty")
    size = fields.get("size", "")
    if size not in ("", "quick", "big"):
        raise ActionError(f"size must be quick or big, not {size!r}")
    line = f"  - {f'[{size}] ' if size else ''}{text}"
    if fields.get("create"):
        _ensure_entry(notes, fields["entry"], fields.get("where", ""), today or date.today())

    def edit(lines: list[str], start: int, end: int) -> str:
        block = left_to_do(lines, start, end)
        if block is None:
            at = _entry_text_end(lines, start, end)
            lines[at:at] = ["- Left to do:", line]
        else:
            lines.insert(block[1], line)
        return f"added: {text}"

    return _edit_entry(notes, fields["entry"], edit, today)


NotesAction = Callable[[NotesConfig, Graph, dict[str, str]], str]

ACTIONS: dict[str, NotesAction] = {
    "todo_done": todo_done,
    "todo_undone": todo_undone,
    "todo_delete": todo_delete,
    "todo_link": todo_link,
    "todo_add": todo_add,
    "ice": ice,
    "thaw": thaw,
    "delete_iced": delete_iced,
}
