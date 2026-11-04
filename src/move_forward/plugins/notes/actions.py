"""The notes plugin's buttons: todos, icing and thawing, posted to `/actions/notes/<action>`.
They only record: deleting or untracking iced branches is git's and git_stack's.

Every action takes the form fields of its button and returns a one-line notice for the page.
A todo is named by its id. A repo must be one the graph has branches of, i.e. one of git's
checkouts. `mfd notes` calls the same functions.
"""

from pathlib import Path

from ...api import (
    ActionError,
    ActionHandler,
    ActionRequest,
    ActionResult,
    Graph,
    Model,
    SourceError,
)
from ...api.kinds import PULL_REQUEST as PR
from ...api.kinds import TICKET
from .config import NotesConfig
from .covers import covers
from .markdown import links_in
from .models import Entry, IcedItem, Link, Size, Todo
from .repository import NotesRepository

ICED_FROM_DASHBOARD_NOTE = "iced from the dashboard"
TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `git.branch` entity's id."""
BRANCH = "git.branch"
STACK_BRANCH = "git_stack.stack_branch"
ENTRY = "notes.entry"
ICEABLE = (PR, TICKET, ENTRY)
"""What `ice_item` puts on ice on its own; branches go with their stacks, through `ice`."""
INFERRED = "inferred"
"""The status of an entry the dashboard made, rather than the skill."""


class _BranchView(Model):
    """What notes reads of a `git.branch` (git's, with what git_stack adds)."""

    id: str
    repo_dir: str | None = None
    """None for a branch only git-stack still tracks."""
    name: str | None = None
    sha: str | None = None


class _StackBranchView(Model):
    """What notes reads of a `git_stack.stack_branch`."""

    repo_dir: str
    name: str
    stack_parent: str
    stack_group: str | None = None


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
    """The branch's whole stack as git-stack sees it, from the `git_stack.stack_branch`es:
    its stack, and the other stacks in its group, since git-stack's `restack`, `sync` and
    `push` act on all of them. Each stack bottom first, the branch's own first of all; just
    the branch when it isn't in a stack (or git_stack isn't on). Only branches git still
    has are iced."""
    here = _branches(graph, repo)
    tracked = [
        b
        for b in graph.view(STACK_BRANCH, _StackBranchView)
        if Path(b.repo_dir) == repo and b.name in here
    ]
    parents = {b.name: b.stack_parent for b in tracked}
    if branch not in parents:
        return [branch]
    bottom, seen = branch, {branch}
    while parents[bottom] in parents and parents[bottom] not in seen:  # up to the root
        bottom = parents[bottom]
        seen.add(bottom)
    groups = {b.name: b.stack_group for b in tracked if b.stack_group}
    group = groups.get(bottom)
    bottoms = [bottom] + sorted(
        name
        for name, other in groups.items()
        if group is not None and other == group and name != bottom and parents[name] not in parents
    )
    children: dict[str, list[str]] = {}
    for name, parent in parents.items():
        children.setdefault(parent, []).append(name)
    stack: list[str] = []
    for start in bottoms:
        pending = [start]
        while pending:
            name = pending.pop()
            if name not in stack:
                stack.append(name)
                pending += reversed(children.get(name, []))
    return stack


def ice(req: ActionRequest[NotesConfig]) -> str:
    """Put the branch's stack on ice, recording each branch's commit as git last saw it. An
    optional `note` says why."""
    ctx, graph, fields = req.ctx, req.graph, req.fields
    repo = _repo(graph, req.field("repo"))
    branch = req.field("branch")
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
    ice_id = notes.add_ice(names[0], ctx.today, note, items)
    notes.replace_covers([ice_id], covers(graph, notes.ices()))
    return f"put {names[0]} on ice ({len(names)} branches)"


def ice_item(req: ActionRequest[NotesConfig]) -> str:
    """Put a PR, ticket or work entry on ice, by `kind` and `id`, with what it links: an
    entry its PRs and tickets, a ticket the PRs that mention it. An optional `note` says
    why."""
    ctx, graph, fields = req.ctx, req.graph, req.fields
    kind, given = req.field("kind"), req.field("id")
    ref_id = graph.identify(kind, given)  # a ticket by its key, too
    if kind not in ICEABLE:
        raise ActionError(f"notes puts PRs, tickets and work entries on ice, not a {kind!r}")
    entity = graph.get(kind, ref_id)
    if entity is None:
        raise ActionError(f"no {kind} {given!r} to put on ice")
    notes = NotesRepository(ctx.db)
    iced = {(item.kind, item.ref_id) for i in notes.ices() for item in i.items}
    if (kind, ref_id) in iced or graph.hidden(entity.ref):
        raise ActionError(f"{entity.title} is already on ice")
    note = fields.get("note") or ICED_FROM_DASHBOARD_NOTE
    item = IcedItem(kind=kind, ref_id=ref_id, name=entity.title)
    ice_id = notes.add_ice(entity.title, ctx.today, note, [item])
    notes.replace_covers([ice_id], covers(graph, notes.ices()))
    return f"put {entity.title} on ice"


def _ice_id(req: ActionRequest[NotesConfig]) -> int:
    try:
        return int(req.field("ice"))
    except ValueError as e:
        raise ActionError(f"no such stack on ice: {req.fields['ice']!r}") from e


def thaw(req: ActionRequest[NotesConfig]) -> str:
    """Take a stack off ice."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    ice_id = _ice_id(req)
    found = next((i for i in notes.ices() if i.id == ice_id), None)
    if found is None:
        raise ActionError("that stack is not on ice")
    notes.delete_ice(ice_id)
    return f"thawed {found.title}"


# Todos


def _todo(
    notes: NotesRepository, req: ActionRequest[NotesConfig], done: bool
) -> tuple[Entry, Todo]:
    """The todo the `todo` field names by id, and its entry; it must be done or open as said."""
    try:
        todo_id = int(req.field("todo"))
    except ValueError as e:
        raise ActionError(f"bad todo id {req.fields['todo']!r}") from e
    for entry in notes.entries():
        for todo in entry.todos:
            if todo.id == todo_id:
                if (todo.done_on is not None) != done:
                    raise ActionError(f"{todo.text!r} isn't {'done' if done else 'open'}")
                return entry, todo
    raise ActionError(f"no todo {todo_id}")


def todo_done(req: ActionRequest[NotesConfig]) -> ActionResult:
    """Record an open todo as done today, and keep it on the focus page to undo."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, req, done=False)
    notes.set_done(todo.id, ctx.today)
    notes.touch(entry.id, ctx.today)
    return ActionResult(f"done: {todo.text}", ctx.kept(str(todo.id)))


def todo_undone(req: ActionRequest[NotesConfig]) -> str:
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, req, done=True)
    notes.set_done(todo.id, None)
    notes.touch(entry.id, ctx.today)
    return f"reopened: {todo.text}"


def todo_delete(req: ActionRequest[NotesConfig]) -> str:
    """Drop an open todo without recording it as done."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, req, done=False)
    notes.delete_todo(todo.id)
    notes.touch(entry.id, ctx.today)
    return f"dropped: {todo.text}"


def link_for(graph: Graph, value: str) -> Link:
    """A ticket key (or another linking entity's handle a plugin declares) or a PR URL as a
    link, by the entity's id. A key the graph doesn't know yet is fetched, so a ticket in a
    project new to jira is found (and jira knows that project's keys from then on)."""
    named = graph.named(value)
    if len(named) == 1 and graph.handle(named[0].kind, named[0].id) == value:
        return Link(kind=named[0].kind, ref=named[0].id)
    found = links_in(value)
    if len(found) == 1 and found[0].ref == value:
        return found[0]
    ticket = graph.identify(TICKET, value)
    try:
        fetched = graph.fetch(TICKET, [ticket])
    except SourceError as e:
        raise ActionError(str(e)) from e
    if ticket in fetched:
        return Link(kind=TICKET, ref=ticket)
    raise ActionError(f"{value!r} is neither a PR URL nor a ticket key Jira knows")


def todo_link(req: ActionRequest[NotesConfig]) -> str:
    """Link an open todo to a Jira ticket or a PR."""
    ctx, graph = req.ctx, req.graph
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, req, done=False)
    value = req.field("input").strip()
    link = link_for(graph, value)
    if link in todo.links:
        raise ActionError(f"{value} is already linked to that todo")
    notes.link_todo(todo.id, link)
    notes.touch(entry.id, ctx.today)
    return f"linked {value}"


def todo_add(req: ActionRequest[NotesConfig]) -> str:
    """Add an open todo to an entry: `input` (or `text`) with an optional `size`, quick or big.

    With `create`, a missing entry is created first (`where` names its PR or ticket).
    """
    ctx, fields = req.ctx, req.fields
    text = (fields.get("input") or fields.get("text") or "").strip()
    if not text:
        raise ActionError("the todo is empty")
    size = fields.get("size", "")
    if size not in ("", "quick", "big"):
        raise ActionError(f"size must be quick or big, not {size!r}")
    notes = NotesRepository(ctx.db)
    title = req.field("entry")
    entry_id = notes.entry_id(title)
    if entry_id is None and fields.get("create"):
        where = fields.get("where", "")
        entry_id = notes.add_entry(title, ctx.today, INFERRED, where or None, links_in(where))
    if entry_id is None:
        raise ActionError(f"no work-notes entry titled {title}")
    sized: Size | None = "quick" if size == "quick" else "big" if size == "big" else None
    notes.add_todo(entry_id, text, sized, links_in(text))
    notes.touch(entry_id, ctx.today)
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
