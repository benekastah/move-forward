"""The notes plugin's buttons: todos, icing and thawing, posted to `/actions/notes/<action>`.
They only record: deleting or untracking iced branches is git's and git_stack's.

Every action takes the form fields of its button and returns a one-line notice for the page.
A todo is named by its id. A repo must be one the graph has branches of, i.e. one of git's
checkouts. `mfd notes` calls the same functions.
"""

from pathlib import Path

from ...domain import Model
from ...graph import Graph
from ...plugin import KEPT, ActionError, ActionHandler, ActionResult, Context
from .config import NotesConfig
from .covers import covers
from .markdown import links_in
from .models import Entry, IcedItem, Link, Size, Todo
from .repository import NotesRepository

ICED_FROM_DASHBOARD_NOTE = "iced from the dashboard"
TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""
BRANCH = "branch"
STACK_BRANCH = "stack_branch"
PR = "pr"
TICKET = "ticket"
ENTRY = "entry"
ICEABLE = (PR, TICKET, ENTRY)
"""What `ice_item` puts on ice on its own; branches go with their stacks, through `ice`."""
INFERRED = "inferred"
"""The status of an entry the dashboard made, rather than the skill."""


class _BranchView(Model):
    """What notes reads of a `branch` (git's, with what git_stack adds)."""

    id: str
    repo_dir: str | None = None
    """None for a branch only git-stack still tracks."""
    name: str | None = None
    sha: str | None = None


class _StackBranchView(Model):
    """What notes reads of a `stack_branch` (git_stack's)."""

    repo_dir: str
    name: str
    stack_parent: str


def _branches(graph: Graph, repo: Path) -> dict[str, _BranchView]:
    """git's branches in `repo`, by name."""
    return {
        b.name: b
        for b in graph.view(BRANCH, _BranchView)
        if b.name and b.repo_dir and Path(b.repo_dir) == repo
    }


def _repo(graph: Graph, repo_dir: str) -> Path:
    """`repo_dir`, if the graph has branches in it (from `[git] repos`)."""
    if not _branches(graph, Path(repo_dir)):
        raise ActionError(f"{repo_dir} is not a configured repo")
    return Path(repo_dir)


def _stack_branches(graph: Graph, repo: Path, branch: str) -> list[str]:
    """The branch's whole stack, bottom first, from git_stack's `stack_branch`es; just the
    branch when it isn't in a stack (or git_stack isn't on). Only branches git still has
    are iced."""
    here = _branches(graph, repo)
    parents = {
        b.name: b.stack_parent
        for b in graph.view(STACK_BRANCH, _StackBranchView)
        if Path(b.repo_dir) == repo and b.name in here
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
    """Put the branch's stack on ice, recording each branch's commit as git last saw it. An
    optional `note` says why."""
    repo = _repo(graph, fields["repo"])
    branch = fields["branch"]
    branches = _branches(graph, repo)
    if branch not in branches:
        raise ActionError(f"{branch} doesn't exist in {repo}")
    notes = NotesRepository(ctx.db)
    names = _stack_branches(graph, repo, branch)
    ref_ids = [branches[name].id for name in names]
    iced = {item.ref_id for i in notes.ices() for item in i.items if item.kind == BRANCH}
    if iced.intersection(ref_ids):
        raise ActionError(f"{branch} is already on ice")
    items: list[IcedItem] = []
    for name in names:
        b = branches[name]
        items.append(IcedItem(kind=BRANCH, ref_id=b.id, name=name, repo_dir=b.repo_dir, sha=b.sha))
    note = fields.get("note") or ICED_FROM_DASHBOARD_NOTE
    ice_id = notes.add_ice(names[0], ctx.now.date(), note, items)
    notes.replace_covers([ice_id], covers(graph, notes.ices()))
    return f"put {names[0]} on ice ({len(names)} branches)"


def ice_item(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Put a PR, ticket or work entry on ice, by `kind` and `id`, with what it links: an
    entry its PRs and tickets, a ticket the PRs that mention it. An optional `note` says
    why."""
    kind, ref_id = fields["kind"], fields["id"]
    if kind not in ICEABLE:
        raise ActionError(f"notes puts PRs, tickets and work entries on ice, not a {kind!r}")
    entity = graph.get(kind, ref_id)
    if entity is None:
        raise ActionError(f"no {kind} {ref_id!r} to put on ice")
    notes = NotesRepository(ctx.db)
    iced = {(item.kind, item.ref_id) for i in notes.ices() for item in i.items}
    if (kind, ref_id) in iced or graph.hidden(entity.ref):
        raise ActionError(f"{entity.title} is already on ice")
    note = fields.get("note") or ICED_FROM_DASHBOARD_NOTE
    item = IcedItem(kind=kind, ref_id=ref_id, name=entity.title)
    ice_id = notes.add_ice(entity.title, ctx.now.date(), note, [item])
    notes.replace_covers([ice_id], covers(graph, notes.ices()))
    return f"put {entity.title} on ice"


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


def _todo(notes: NotesRepository, fields: dict[str, str], done: bool) -> tuple[Entry, Todo]:
    """The todo the `todo` field names by id, and its entry; it must be done or open as said."""
    try:
        todo_id = int(fields["todo"])
    except ValueError as e:
        raise ActionError(f"bad todo id {fields['todo']!r}") from e
    for entry in notes.entries():
        for todo in entry.todos:
            if todo.id == todo_id:
                if (todo.done_on is not None) != done:
                    raise ActionError(f"{todo.text!r} isn't {'done' if done else 'open'}")
                return entry, todo
    raise ActionError(f"no todo {todo_id}")


def todo_done(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> ActionResult:
    """Record an open todo as done today, and keep it on the focus page to undo."""
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, fields, done=False)
    notes.set_done(todo.id, ctx.now.date())
    notes.touch(entry.id, ctx.now.date())
    return ActionResult(f"done: {todo.text}", {KEPT: f"notes:{todo.id}"})


def todo_undone(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, fields, done=True)
    notes.set_done(todo.id, None)
    notes.touch(entry.id, ctx.now.date())
    return f"reopened: {todo.text}"


def todo_delete(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Drop an open todo without recording it as done."""
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, fields, done=False)
    notes.delete_todo(todo.id)
    notes.touch(entry.id, ctx.now.date())
    return f"dropped: {todo.text}"


def link_for(graph: Graph, value: str) -> Link:
    """A ticket key (or another linking entity's id a plugin declares) or a PR URL as a link."""
    named = graph.named(value)
    if [r.id for r in named] == [value]:
        return Link(kind=named[0].kind, ref=value)
    found = links_in(value)
    if len(found) == 1 and found[0].ref == value:
        return found[0]
    raise ActionError(f"{value!r} is neither a PR URL nor a ticket key (with [jira] set up)")


def todo_link(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Link an open todo to a Jira ticket or a PR."""
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, fields, done=False)
    link = link_for(graph, fields["input"].strip())
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
        entry_id = notes.add_entry(title, ctx.now.date(), INFERRED, where or None, links_in(where))
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
    "ice_item": ice_item,
    "thaw": thaw,
}
