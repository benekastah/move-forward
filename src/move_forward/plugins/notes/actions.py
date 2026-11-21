"""The notes plugin's buttons: todos, icing and thawing, posted to `/actions/notes/<action>`.
They only record: deleting or untracking iced branches is git's and git_stack's.

Every action takes the form fields of its button and returns a one-line notice for the page.
A todo is named by its id. A repo must be one the graph has branches of, i.e. one of git's
checkouts. `mfd notes` calls the same functions.
"""

from pathlib import Path

from move_forward.api import (
    ActionError,
    ActionHandler,
    ActionRequest,
    Graph,
    Model,
    Ref,
    SourceError,
)
from move_forward.api.ids import branch_parts
from move_forward.api.kinds import PULL_REQUEST as PR
from move_forward.api.kinds import TICKET
from move_forward.plugins.notes.config import NotesConfig
from move_forward.plugins.notes.covers import covers
from move_forward.plugins.notes.models import Condition, Entry, IcedItem, Link, Size, Todo
from move_forward.plugins.notes.repository import NotesRepository
from move_forward.plugins.notes.text import PR_URL_RE, links_in, sized

ICED_FROM_DASHBOARD_NOTE = "iced from the dashboard"
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
    """None for a branch only git stack still tracks."""
    name: str | None = None
    sha: str | None = None


class _StackBranchView(Model):
    """What notes reads of a `git_stack.stack_branch`: what git stack acts on with it."""

    stack: list[str]


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
    """The branch's whole stack as git stack sees it (`git_stack.stack_branch`'s `stack`): its
    stack, and the other stacks in its group, since git stack's `restack`, `sync` and `push`
    act on all of them; just the branch when it isn't in a stack (or git_stack isn't on).
    Only branches git still has are iced."""
    here = _branches(graph, repo)
    tracked = graph.view_one(STACK_BRANCH, here[branch].id, _StackBranchView)
    if tracked is None:
        return [branch]
    names = [branch_parts(id)[1] for id in tracked.stack]
    return [name for name in names if name in here]


def ice(req: ActionRequest[NotesConfig]) -> str:
    """Put the branch's stack on ice, recording each branch's commit as git last saw it. An
    optional `note` says why."""
    ctx, graph, fields = req.ctx, req.graph, req.form.fields
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
    note = (fields.get("note") or "").strip() or ICED_FROM_DASHBOARD_NOTE
    ice_id = notes.add_ice(names[0], ctx.today, note, items)
    notes.replace_covers([ice_id], covers(graph, notes.ices()))
    return f"put {names[0]} on ice ({len(names)} branches)"


def ice_item(req: ActionRequest[NotesConfig]) -> str:
    """Put a PR, ticket or work entry on ice, by `kind` and `id`, with what it links: an
    entry its PRs and tickets, a ticket the PRs that mention it (whose publisher lets their
    text hide them: mine, not those I'm asked to review). An optional `note` says why."""
    ctx, graph, fields = req.ctx, req.graph, req.form.fields
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
    note = (fields.get("note") or "").strip() or ICED_FROM_DASHBOARD_NOTE
    item = IcedItem(kind=kind, ref_id=ref_id, name=entity.title)
    ice_id = notes.add_ice(entity.title, ctx.today, note, [item])
    notes.replace_covers([ice_id], covers(graph, notes.ices()))
    return f"put {entity.title} on ice"


def _ice_id(req: ActionRequest[NotesConfig]) -> int:
    try:
        return int(req.field("ice"))
    except ValueError as e:
        raise ActionError(f"nothing on ice has the id {req.form.fields['ice']!r}") from e


def ice_note(req: ActionRequest[NotesConfig]) -> str:
    """Say why something is on ice: `input`, in place of what's said now."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    note = req.field("input").strip()
    if not note:
        raise ActionError("the note is empty")
    title = notes.set_ice_note(_ice_id(req), note)
    if title is None:
        raise ActionError("that is not on ice")
    return f"noted why {title} is on ice"


def thaw(req: ActionRequest[NotesConfig]) -> str:
    """Take what's on ice (`ice`, its id) off it: a stack, a PR, a ticket or a work entry."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    ice_id = _ice_id(req)
    found = next((i for i in notes.ices() if i.id == ice_id), None)
    if found is None:
        raise ActionError("that is not on ice")
    notes.delete_ice(ice_id)
    return f"thawed {found.title}"


# Entries

ENTRY_FIELDS = ("status", "where", "blocked-on")
"""What `entry_set` sets of an entry, as `mfd notes set` names them."""


def _entry(notes: NotesRepository, req: ActionRequest[NotesConfig]) -> Entry:
    """The entry the `entry` field names by title."""
    title = req.field("entry")
    found = next((e for e in notes.entries() if e.title == title), None)
    if found is None:
        raise ActionError(f"no work-notes entry titled {title}")
    return found


def entry_set(req: ActionRequest[NotesConfig]) -> str:
    """Set an entry's `status`, `where` (where the work lives) or `blocked-on`, named by
    `field` (or `choice`, from its drop-down), to `input` (or `text`); empty clears it."""
    ctx, fields = req.ctx, req.form.fields
    notes = NotesRepository(ctx.db)
    entry = _entry(notes, req)
    field = fields.get("field") or fields.get("choice") or ""
    if field not in ENTRY_FIELDS:
        raise ActionError(f"an entry's status, where or blocked-on is set, not {field!r}")
    text = (fields.get("input") or fields.get("text") or "").strip() or None
    {"status": notes.set_status, "where": notes.set_where, "blocked-on": notes.set_blocked_on}[
        field
    ](entry.id, text)
    notes.touch(entry.id, ctx.today)
    return f"{entry.title}: {field} is now {text}" if text else f"{entry.title}: {field} cleared"


def entry_link(req: ActionRequest[NotesConfig]) -> str:
    """Say an entry is about a PR or ticket: `input`, a PR URL or ticket key."""
    ctx, graph = req.ctx, req.graph
    notes = NotesRepository(ctx.db)
    entry = _entry(notes, req)
    value = req.field("input").strip()
    if not notes.link_entry(entry.id, link_for(graph, value)):
        raise ActionError(f"{entry.title} is already about {value}")
    notes.touch(entry.id, ctx.today)
    return f"{entry.title}: linked {value}"


def entry_remove(req: ActionRequest[NotesConfig]) -> str:
    """Forget an entry with its todos, for finished work: nothing is recorded as done."""
    notes = NotesRepository(req.ctx.db)
    entry = _entry(notes, req)
    notes.delete_entry(entry.id)
    return f"removed {entry.title}"


# Merge conditions


def _pr_url(req: ActionRequest[NotesConfig]) -> str:
    pr = req.field("pr").strip()
    if not PR_URL_RE.fullmatch(pr):
        raise ActionError(f"{pr!r} isn't a pull request's URL")
    return pr


def _pr_name(pr: str) -> str:
    return f"PR {pr.rsplit('/', 1)[1]}"


def condition_add(req: ActionRequest[NotesConfig]) -> str:
    """Hold a PR (`pr`, its URL) until `input` happens: a merge condition on the entry titled
    `entry`; with `create`, one is made first, about the PR."""
    ctx = req.ctx
    pr, text = _pr_url(req), req.field("input").strip()
    if not text:
        raise ActionError("the merge condition is empty")
    notes = NotesRepository(ctx.db)
    title = req.field("entry")
    entry_id = notes.entry_id(title)
    if entry_id is None and req.form.fields.get("create"):
        entry_id = notes.add_entry(entry_title(title), ctx.today, INFERRED, pr, links_in(pr))
    if entry_id is None:
        raise ActionError(f"no work-notes entry titled {title}")
    if not notes.add_condition(entry_id, Condition(pr=pr, text=text)):
        raise ActionError(f"{title} already has that merge condition on {_pr_name(pr)}")
    notes.touch(entry_id, ctx.today)
    return f"held {_pr_name(pr)}: {text}"


def condition_remove(req: ActionRequest[NotesConfig]) -> str:
    """Release a PR (`pr`) from the merge condition `choice` (or `text`), in every entry that
    holds it so."""
    ctx, fields = req.ctx, req.form.fields
    pr = _pr_url(req)
    text = fields.get("choice") or fields.get("text") or ""
    notes = NotesRepository(ctx.db)
    holding = [e for e in notes.entries() if Condition(pr=pr, text=text) in e.conditions]
    if not holding:
        raise ActionError(f"{_pr_name(pr)} has no merge condition {text!r}")
    for entry in holding:
        notes.remove_condition(entry.id, text, pr)
        notes.touch(entry.id, ctx.today)
    return f"released {_pr_name(pr)} from: {text}"


# Todos


def _todo(
    notes: NotesRepository, req: ActionRequest[NotesConfig], done: bool
) -> tuple[Entry, Todo]:
    """The todo the `todo` field names by id, and its entry; it must be done or open as said."""
    try:
        todo_id = int(req.field("todo"))
    except ValueError as e:
        raise ActionError(f"bad todo id {req.form.fields['todo']!r}") from e
    for entry in notes.entries():
        for todo in entry.todos:
            if todo.id == todo_id:
                if (todo.done_on is not None) != done:
                    raise ActionError(f"{todo.text!r} isn't {'done' if done else 'open'}")
                return entry, todo
    raise ActionError(f"no todo {todo_id}")


def todo_done(req: ActionRequest[NotesConfig]) -> str:
    """Record an open todo as done today."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, req, done=False)
    notes.set_done(todo.id, ctx.today)
    notes.touch(entry.id, ctx.today)
    return f"done: {todo.text}"


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


def _size(size: str) -> Size | None:
    """A todo's size from a form: quick, big, or blank for none."""
    if size not in ("", "quick", "big"):
        raise ActionError(f"size must be quick or big, not {size!r}")
    return "quick" if size == "quick" else "big" if size == "big" else None


def todo_size(req: ActionRequest[NotesConfig]) -> str:
    """Say how big an open todo is: `size`, quick or big."""
    ctx = req.ctx
    notes = NotesRepository(ctx.db)
    entry, todo = _todo(notes, req, done=False)
    size = _size(req.field("size"))
    if size is None:
        raise ActionError("size must be quick or big")
    if todo.size == size:
        return f"{todo.text} is already {size}"
    notes.set_todo_size(todo.id, size)
    notes.touch(entry.id, ctx.today)
    return f"{todo.text} is {size}"


def todo_add(req: ActionRequest[NotesConfig]) -> str:
    """Add an open todo to an entry: `input` (or `text`) with an optional size, quick or big, as
    `size` (or `choice`, from its drop-down), or a leading `[quick]` or `[big]` in it, as the
    notes write a size, so it reads back the same.

    With `create`, a missing entry is created first (`where` names its PR or ticket).
    """
    ctx, fields = req.ctx, req.form.fields
    text, written = sized((fields.get("input") or fields.get("text") or "").strip())
    text = text.strip()
    if not text:
        raise ActionError("the todo is empty")
    size = _size(fields.get("size") or fields.get("choice") or "") or written
    notes = NotesRepository(ctx.db)
    title = req.field("entry")
    entry_id = notes.entry_id(title)
    if entry_id is None and fields.get("create"):
        entry_title(title)
        where = fields.get("where", "")
        entry_id = notes.add_entry(title, ctx.today, INFERRED, where or None, links_in(where))
    if entry_id is None:
        raise ActionError(f"no work-notes entry titled {title}")
    notes.add_todo(entry_id, text, size, links_in(text))
    notes.touch(entry_id, ctx.today)
    return f"added: {text}"


def entry_title(text: str) -> str:
    """`text` as a new entry's title, refused when it's empty or more than one line (any
    break `splitlines` takes)."""
    if not text.strip():
        raise ActionError("an entry's title is empty")
    if len(text.splitlines()) > 1:
        raise ActionError("an entry's title is one line")
    return text


def todo_new(req: ActionRequest[NotesConfig]) -> str:
    """Start a todo from nothing: `input` as a new entry's title and its one todo, sized by
    `choice` (from its drop-down), or by a leading `[quick]` or `[big]`, as the notes write a
    size, so it reads back the same; as notes alone is a todo list. An entry of that title gets
    the todo instead, but not one on ice, where it would be hidden, nor twice."""
    ctx, graph = req.ctx, req.graph
    text, written = sized((req.form.fields.get("input") or "").strip())
    if not text.strip():
        raise ActionError("the todo is empty")
    text = entry_title(text.strip())
    if graph.hidden(Ref(kind=ENTRY, id=text)):
        raise ActionError(f"{text!r} is an entry on ice: thaw it, or name the todo otherwise")
    notes = NotesRepository(ctx.db)
    entry_id = notes.entry_id(text)
    if entry_id is None:
        entry_id = notes.add_entry(text, ctx.today, INFERRED, None, links_in(text))
    elif any(t.text == text for e in notes.entries() if e.title == text for t in e.open_todos):
        return f"already a todo: {text}"
    chosen = _size(req.form.fields.get("choice") or "") or written
    notes.add_todo(entry_id, text, chosen, links_in(text))
    notes.touch(entry_id, ctx.today)
    return f"added: {text}"


ACTIONS: dict[str, ActionHandler] = {
    "entry_set": entry_set,
    "entry_link": entry_link,
    "entry_remove": entry_remove,
    "condition_add": condition_add,
    "condition_remove": condition_remove,
    "todo_new": todo_new,
    "todo_done": todo_done,
    "todo_undone": todo_undone,
    "todo_delete": todo_delete,
    "todo_link": todo_link,
    "todo_add": todo_add,
    "todo_size": todo_size,
    "ice": ice,
    "ice_item": ice_item,
    "ice_note": ice_note,
    "thaw": thaw,
}
