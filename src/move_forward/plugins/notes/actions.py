"""The notes plugin's buttons: todos, icing and thawing, posted to `/actions/notes/<action>`.
They only record: deleting or untracking iced branches is git's and git_stack's.

Every action takes the form fields of its button and returns a one-line notice for the page.
A todo is named by its entry's title and its text, as the page shows them. A repo must be
one the graph has branches of, i.e. one of git's checkouts.
"""

from pathlib import Path

from ...graph import Graph
from ...plugin import ActionError, ActionHandler, Context
from .config import NotesConfig
from .markdown import JIRA_KEY_RE, links_in
from .models import Entry, IcedItem, Link, Size, Todo
from .repository import NotesRepository

ICED_FROM_DASHBOARD_NOTE = "iced from the dashboard"
TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""
BRANCH = "branch"
PR = "pr"
TICKET = "ticket"
INFERRED = "inferred"
"""The status of an entry the dashboard made, rather than the skill."""


def _repo(graph: Graph, repo_dir: str) -> Path:
    """`repo_dir`, if the graph has branches in it (from `[git] repos`)."""
    prefix = repo_dir + TARGET_SEPARATOR
    if not any(b.ref.id.startswith(prefix) for b in graph.of_kind(BRANCH)):
        raise ActionError(f"{repo_dir} is not a configured repo")
    return Path(repo_dir)


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


def ice(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Put the branch's stack on ice, recording each branch's commit as git last saw it."""
    repo = _repo(graph, fields["repo"])
    branch = fields["branch"]
    if graph.get(BRANCH, f"{repo}{TARGET_SEPARATOR}{branch}") is None:
        raise ActionError(f"{branch} doesn't exist in {repo}")
    notes = NotesRepository(ctx.db)
    names = _stack_branches(graph, repo, branch)
    ref_ids = [f"{repo}{TARGET_SEPARATOR}{name}" for name in names]
    iced = {item.ref_id for i in notes.ices() for item in i.items if item.kind == BRANCH}
    if iced.intersection(ref_ids):
        raise ActionError(f"{branch} is already on ice")
    items: list[IcedItem] = []
    for ref_id in ref_ids:
        entity = graph.get(BRANCH, ref_id)
        sha = entity.attrs.get("sha") if entity else None
        items.append(
            IcedItem(kind=BRANCH, ref_id=ref_id, sha=sha if isinstance(sha, str) else None)
        )
    notes.add_ice(names[0], ctx.now.date(), ICED_FROM_DASHBOARD_NOTE, items)
    return f"put {names[0]} on ice ({len(names)} branches)"


def _ice_id(fields: dict[str, str]) -> int:
    try:
        return int(fields["ice"])
    except ValueError as e:
        raise ActionError(f"no such stack on ice: {fields['ice']!r}") from e


def thaw(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Take a stack off ice."""
    notes = NotesRepository(ctx.db)
    ice_id = _ice_id(fields)
    found = next((i for i in notes.ices() if i.id == ice_id), None)
    if found is None:
        raise ActionError("that stack is not on ice")
    notes.delete_ice(ice_id)
    return f"thawed {found.title}"


# Todos


def _entry(notes: NotesRepository, title: str) -> Entry:
    found = next((e for e in notes.entries() if e.title == title), None)
    if found is None:
        raise ActionError(f"no work-notes entry titled {title}")
    return found


def _todo(entry: Entry, text: str, done: bool) -> Todo:
    found = next(
        (t for t in entry.todos if t.text == text and (t.done_on is not None) == done), None
    )
    if found is None:
        raise ActionError(f"no {'done' if done else 'open'} todo {text!r} in {entry.title}")
    return found


def todo_done(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    notes = NotesRepository(ctx.db)
    entry = _entry(notes, fields["entry"])
    notes.set_done(_todo(entry, fields["text"], done=False).id, ctx.now.date())
    notes.touch(entry.id, ctx.now.date())
    return f"done: {fields['text']}"


def todo_undone(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    notes = NotesRepository(ctx.db)
    entry = _entry(notes, fields["entry"])
    notes.set_done(_todo(entry, fields["text"], done=True).id, None)
    notes.touch(entry.id, ctx.now.date())
    return f"reopened: {fields['text']}"


def todo_delete(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Drop an open todo without recording it as done."""
    notes = NotesRepository(ctx.db)
    entry = _entry(notes, fields["entry"])
    notes.delete_todo(_todo(entry, fields["text"], done=False).id)
    notes.touch(entry.id, ctx.now.date())
    return f"dropped: {fields['text']}"


def _link(graph: Graph, value: str) -> Link:
    """A ticket key or a PR URL as a link; a key needs a plugin that knows tickets."""
    if JIRA_KEY_RE.fullmatch(value):
        if graph.url_for(TICKET, value) is None:
            raise ActionError("no [jira] site configured to link ticket keys")
        return Link(kind=TICKET, ref=value)
    found = links_in(value)
    if len(found) == 1 and found[0].kind == PR and found[0].ref == value:
        return found[0]
    raise ActionError(f"{value!r} is neither a Jira key nor a PR URL")


def todo_link(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Link an open todo to a Jira ticket or a PR."""
    notes = NotesRepository(ctx.db)
    entry = _entry(notes, fields["entry"])
    todo = _todo(entry, fields["text"], done=False)
    link = _link(graph, fields["input"].strip())
    if link in todo.links:
        raise ActionError(f"{link.ref} is already linked to that todo")
    notes.link_todo(todo.id, link)
    notes.touch(entry.id, ctx.now.date())
    return f"linked {link.ref}"


def todo_add(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Add an open todo to an entry: `input` (or `text`) with an optional `size`, quick or big.

    With `create`, a missing entry is created first (`where` names its PR or ticket).
    """
    text = (fields.get("input") or fields.get("text") or "").strip()
    if not text:
        raise ActionError("the todo is empty")
    size = fields.get("size", "")
    if size not in ("", "quick", "big"):
        raise ActionError(f"size must be quick or big, not {size!r}")
    notes = NotesRepository(ctx.db)
    title = fields["entry"]
    entry_id = notes.entry_id(title)
    if entry_id is None and fields.get("create"):
        where = fields.get("where", "")
        entry_id = notes.add_entry(
            title, ctx.now.date(), INFERRED, where or None, links_in(where, title)
        )
    if entry_id is None:
        raise ActionError(f"no work-notes entry titled {title}")
    sized: Size | None = "quick" if size == "quick" else "big" if size == "big" else None
    notes.add_todo(entry_id, text, sized, links_in(text))
    notes.touch(entry_id, ctx.now.date())
    return f"added: {text}"


ACTIONS: dict[str, ActionHandler] = {
    "todo_done": todo_done,
    "todo_undone": todo_undone,
    "todo_delete": todo_delete,
    "todo_link": todo_link,
    "todo_add": todo_add,
    "ice": ice,
    "thaw": thaw,
}
