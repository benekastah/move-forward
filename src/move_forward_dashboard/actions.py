"""The actions the page's buttons run: branch cleanup, pushes, WIP commits, icing and thawing.

Every action takes the form fields of its button, checks them against the config (a repo must
be one of the configured checkouts) and returns a one-line notice for the page. Nothing here
ever forces a push, resets, or stashes.
"""

import contextlib
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import local
from .config import Config
from .domain import IcedStack
from .notes import ON_ICE_FILE, WORK_NOTES_FILE, parse_on_ice
from .shell import CommandError, git

STACK_CLEAN_TIMEOUT_SECONDS = 600
"""`git stack clean` fetches and checks every stack, which takes a while in a big repo."""
TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch in the `targets` field of a bulk action, one per line."""
WIP_MESSAGE = "WIP"
ICED_FROM_DASHBOARD_NOTE = "iced from the dashboard"


class ActionError(Exception):
    """Something the user needs to know; shown on the page, not a crash."""


def _repo(config: Config, repo_dir: str) -> Path:
    path = Path(repo_dir)
    if path not in config.repos:
        raise ActionError(f"{repo_dir} is not a configured repo")
    return path


def _branch_exists(repo: Path, branch: str) -> bool:
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except CommandError:
        return False
    return True


def _checked_out_in(repo: Path, branch: str) -> str | None:
    for path, checked_out in local.parse_worktrees(git(repo, "worktree", "list", "--porcelain")):
        if checked_out == branch:
            return path
    return None


def delete_branch(config: Config, fields: dict[str, str]) -> str:
    repo = _repo(config, fields["repo"])
    branch = fields["branch"]
    if not _branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    if worktree := _checked_out_in(repo, branch):
        raise ActionError(f"{branch} is checked out in {worktree}; switch it away first")
    git(repo, "branch", "-D", branch)
    return f"deleted {branch} in {repo}"


def delete_branches(config: Config, fields: dict[str, str]) -> str:
    """Delete every `<repo>\\t<branch>` line of `targets`; one failure doesn't stop the rest."""
    done: list[str] = []
    failed: list[str] = []
    for line in fields.get("targets", "").splitlines():
        repo_dir, _, branch = line.partition(TARGET_SEPARATOR)
        if not branch:
            continue
        try:
            delete_branch(config, {"repo": repo_dir, "branch": branch})
            done.append(branch)
        except (ActionError, CommandError) as e:
            failed.append(f"{branch}: {e}")
    notice = f"deleted {len(done)} branches"
    if failed:
        notice += "; failed: " + "; ".join(failed)
    return notice


def push_branch(config: Config, fields: dict[str, str]) -> str:
    """Push a branch, setting its upstream if it has none. Never forces."""
    repo = _repo(config, fields["repo"])
    branch = fields["branch"]
    if not _branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"{branch}@{{upstream}}")
        has_upstream = True
    except CommandError:
        has_upstream = False
    if has_upstream:
        git(repo, "push", "origin", f"{branch}:{branch}")
    else:
        git(repo, "push", "--set-upstream", "origin", branch)
    return f"pushed {branch}"


def wip_commit(config: Config, fields: dict[str, str]) -> str:
    """Commit the modified tracked files of a worktree as "WIP". Untracked files are left."""
    _repo(config, fields["repo"])
    worktree = Path(fields["worktree"])
    if not (worktree / ".git").exists():
        raise ActionError(f"{worktree} is not a worktree")
    modified, _ = local.parse_status(git(worktree, "status", "--porcelain"))
    if not modified:
        raise ActionError(f"{worktree} has no modified tracked files")
    git(worktree, "commit", "--all", "--quiet", "-m", WIP_MESSAGE)
    return f"committed {modified} files as {WIP_MESSAGE} in {worktree}"


def _tilde(path: Path) -> str:
    home = Path.home()
    return "~/" + str(path.relative_to(home)) if path.is_relative_to(home) else str(path)


def _stack_branches(repo: Path, branch: str) -> list[str]:
    """The branch's whole git-stack stack, bottom first, or just the branch."""
    try:
        branches = git(repo, "stack", "show-stack", branch).split()
    except (CommandError, FileNotFoundError):
        return [branch]
    return branches or [branch]


def ice(config: Config, fields: dict[str, str], today: date | None = None) -> str:
    """Append the branch's stack to on-ice.md, as the move-forward skill records it."""
    repo = _repo(config, fields["repo"])
    branch = fields["branch"]
    if not _branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    path = config.notes_dir / ON_ICE_FILE
    text = path.read_text() if path.exists() else "# Stacks on ice\n"
    branches = _stack_branches(repo, branch)
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


def _iced_stacks(config: Config) -> list[IcedStack]:
    path = config.notes_dir / ON_ICE_FILE
    return parse_on_ice(path.read_text()) if path.exists() else []


def stack_clean(config: Config, fields: dict[str, str]) -> str:
    """`git stack clean -d local`: untrack merged branches in every stack and delete them.

    Refused while the repo has iced stacks, since clean touches every stack (the skill's rule).
    """
    repo = _repo(config, fields["repo"])
    iced = [s for s in _iced_stacks(config) if Path(s.repo_dir).expanduser() == repo]
    if iced:
        names = ", ".join(s.bottom_branch for s in iced)
        raise ActionError(f"{repo} has iced stacks ({names}); clean per stack instead")
    out = git(repo, "stack", "clean", "-d", "local", timeout=STACK_CLEAN_TIMEOUT_SECONDS)
    deleted = sum(1 for line in out.splitlines() if "deleted" in line.lower())
    return f"ran git stack clean -d local in {repo}" + (f", {deleted} deleted" if deleted else "")


def delete_iced(config: Config, fields: dict[str, str]) -> str:
    """Delete an iced stack's local branches and drop it from on-ice.md.

    Checks every branch first, so a branch checked out in a worktree stops it before anything
    is deleted. Remote branches and PRs are left alone.
    """
    bottom = fields["branch"]
    stack = next((s for s in _iced_stacks(config) if s.bottom_branch == bottom), None)
    if stack is None:
        raise ActionError(f"{bottom} is not on ice")
    repo = _repo(config, str(Path(stack.repo_dir).expanduser()))
    names = [b.name for b in stack.branches]
    existing = [name for name in names if _branch_exists(repo, name)]
    for name in existing:
        if worktree := _checked_out_in(repo, name):
            raise ActionError(f"{name} is checked out in {worktree}; switch it away first")
    if existing:
        # Not tracked by git-stack, or git-stack isn't installed: nothing to untrack.
        with contextlib.suppress(CommandError, FileNotFoundError):
            git(repo, "stack", "untrack", *existing)
        git(repo, "branch", "-D", *existing)
    thaw(config, {"branch": bottom})
    return f"deleted {len(existing)} branches of {bottom} and took it off ice"


def thaw(config: Config, fields: dict[str, str]) -> str:
    """Remove a stack's entry from on-ice.md."""
    bottom = fields["branch"]
    path = config.notes_dir / ON_ICE_FILE
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
    config: Config, entry: str, edit: Callable[[list[str], int, int], str], today: date | None
) -> str:
    """Apply `edit` to the entry's lines in work-notes.md, bump its Updated date, and save."""
    path = config.notes_dir / WORK_NOTES_FILE
    if not path.exists():
        raise ActionError(f"{path} doesn't exist")
    lines = path.read_text().split("\n")
    start, end = entry_bounds(lines, entry)
    notice = edit(lines, start, end)
    start, end = entry_bounds(lines, entry)
    _touch_updated(lines, start, end, today or date.today())
    path.write_text("\n".join(lines))
    return notice


def todo_done(config: Config, fields: dict[str, str], today: date | None = None) -> str:
    """Move an open todo to a `Done <today>:` line of its entry."""
    text = fields["text"]

    def edit(lines: list[str], start: int, end: int) -> str:
        size = _pop_todo(lines, start, end, text)
        block = left_to_do(lines, start, end - 1)
        marker = f"[{size}] " if size else ""
        at = block[1] if block else _entry_text_end(lines, start, end - 1)
        lines.insert(at, f"- Done {(today or date.today()).isoformat()}: {marker}{text}")
        return f"done: {text}"

    return _edit_entry(config, fields["entry"], edit, today)


def todo_undone(config: Config, fields: dict[str, str], today: date | None = None) -> str:
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

    return _edit_entry(config, fields["entry"], edit, today)


def todo_delete(config: Config, fields: dict[str, str], today: date | None = None) -> str:
    """Drop an open todo from its entry without recording it as done."""
    text = fields["text"]

    def edit(lines: list[str], start: int, end: int) -> str:
        _pop_todo(lines, start, end, text)
        return f"dropped: {text}"

    return _edit_entry(config, fields["entry"], edit, today)


JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def _link_url(config: Config, value: str) -> str:
    """A Jira key as its browse URL, or a URL as given."""
    if JIRA_KEY_RE.fullmatch(value):
        if config.jira is None:
            raise ActionError("no [jira] site configured to link ticket keys")
        return f"{config.jira.site.rstrip('/')}/browse/{value}"
    if value.startswith(("https://", "http://")) and " " not in value:
        return value
    raise ActionError(f"{value!r} is neither a Jira key nor a URL")


def todo_link(config: Config, fields: dict[str, str], today: date | None = None) -> str:
    """Append a Jira ticket's or PR's URL to an open todo's line."""
    text = fields["text"]
    url = _link_url(config, fields["input"].strip())

    def edit(lines: list[str], start: int, end: int) -> str:
        if url in text:
            raise ActionError(f"{url} is already in that todo")
        i, _ = _find_todo(lines, start, end, text)
        lines[i] = lines[i].rstrip() + " " + url
        return f"linked {url}"

    return _edit_entry(config, fields["entry"], edit, today)


def _ensure_entry(config: Config, entry: str, where: str, today: date) -> None:
    """Append a work-notes entry titled `entry` if there is none, as the skill would."""
    path = config.notes_dir / WORK_NOTES_FILE
    text = path.read_text() if path.exists() else "# Work notes\n"
    if re.search(rf"^## {re.escape(entry)}\s*$", text, re.MULTILINE):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["", f"## {entry}", "- Status: inferred"]
    if where:
        lines.append(f"- Where: {where}")
    lines += ["- Left to do:", f"- Updated: {today.isoformat()}"]
    path.write_text(text.rstrip("\n") + "\n" + "\n".join(lines) + "\n")


def todo_add(config: Config, fields: dict[str, str], today: date | None = None) -> str:
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
        _ensure_entry(config, fields["entry"], fields.get("where", ""), today or date.today())

    def edit(lines: list[str], start: int, end: int) -> str:
        block = left_to_do(lines, start, end)
        if block is None:
            at = _entry_text_end(lines, start, end)
            lines[at:at] = ["- Left to do:", line]
        else:
            lines.insert(block[1], line)
        return f"added: {text}"

    return _edit_entry(config, fields["entry"], edit, today)


def redirect_params(name: str, fields: dict[str, str]) -> dict[str, str]:
    """Extra query parameters for the page an action redirects back to, once it succeeded.

    Ticking a todo off keeps it on the focus page, box ticked, so the tick can be undone.
    """
    if name == "todo_done":
        return {"done_entry": fields["entry"], "done_text": fields["text"]}
    return {}


ACTIONS: dict[str, Callable[[Config, dict[str, str]], str]] = {
    "todo_done": todo_done,
    "todo_undone": todo_undone,
    "todo_delete": todo_delete,
    "todo_link": todo_link,
    "todo_add": todo_add,
    "delete_branch": delete_branch,
    "delete_branches": delete_branches,
    "push_branch": push_branch,
    "wip_commit": wip_commit,
    "ice": ice,
    "thaw": thaw,
    "stack_clean": stack_clean,
    "delete_iced": delete_iced,
}


def run(name: str, config: Config, fields: dict[str, str]) -> str:
    """Run a named action; unknown names and missing fields are ActionErrors."""
    action = ACTIONS.get(name)
    if action is None:
        raise ActionError(f"unknown action {name}")
    try:
        return action(config, fields)
    except KeyError as e:
        raise ActionError(f"missing field {e}") from e
    except CommandError as e:
        raise ActionError(str(e)) from e
