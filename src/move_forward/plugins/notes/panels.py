"""The notes plugin's panels: todos from the work notes and stacks on ice, and what it puts on
other plugins' items: Add todo on items about my PRs and tickets, Put on ice on items about a
branch. Pure functions; no I/O."""

import re
from datetime import datetime

from ...domain import Model
from ...graph import Entity, Graph, Ref, attrs
from ...page import Action, Item, Link, Panel, PrState
from .kinds import EntryAttrs, IcedBranchAttrs
from .markdown import PR_URL_RE
from .models import Entry, Ice, Todo

NOT_BLOCKED_RE = re.compile(r"^\s*(nothing|none|no|-|n/a)\b", re.IGNORECASE)

PR = "pr"
TICKET = "ticket"
ENTRY = "entry"
ICED_BRANCH = "iced_branch"
BRANCH = "branch"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""
TODO_ADD = "notes/todo_add"
PR_STATES: dict[str, PrState] = {"open": "open", "merged": "merged", "closed": "closed"}


# Output models


def todo_actions(todo: Todo, entry: str, suggested: bool = False) -> list[Action]:
    """A todo's checkbox (Done, or undone once it's done) and, while open, its Delete, Link and
    Add todo buttons. The todo is addressed by id.

    `suggested` draws the Done box bigger, when every PR the todo is about is merged or closed.
    """
    fields = {"todo": str(todo.id)}
    if todo.done_on is not None:
        return [
            Action(
                name="notes/todo_undone", label="Done", fields=fields, checkbox=True, checked=True
            )
        ]
    return [
        Action(
            name="notes/todo_done", label="Done", fields=fields, checkbox=True, suggested=suggested
        ),
        Action(
            name="notes/todo_delete",
            label="Delete todo",
            fields=fields,
            confirm=f"Drop the todo “{todo.text}” from {entry}? It isn't recorded as done.",
        ),
        Action(name="notes/todo_link", label="Link", fields=fields, input="Jira key or PR URL"),
        add_todo_action(entry),
    ]


ADD_TODO_INPUT = "New todo"


def add_todo_action(entry: str | None, new_title: str = "", where: str = "") -> Action:
    """An Add todo field for a work-notes entry, or for one to create titled `new_title`."""
    if entry is not None:
        return Action(
            name=TODO_ADD, label="Add todo", fields={"entry": entry}, input=ADD_TODO_INPUT
        )
    return Action(
        name=TODO_ADD,
        label="Add todo",
        fields={"entry": new_title, "create": "1", "where": where},
        input=ADD_TODO_INPUT,
    )


def entry_for(entries: list[Entry], url: str | None, keys: list[str], graph: Graph) -> str | None:
    """The entry that covers a PR or ticket: one that names the URL, else one about one of
    the tickets."""
    if url:
        for entry in entries:
            if url in _entry_urls(entry):
                return entry.title
    for key in keys:
        for entry in entries:
            if key in entry_keys(entry, graph):
                return entry.title
    return None


def _is_blocked(entry: Entry) -> bool:
    return bool(entry.blocked_on) and not NOT_BLOCKED_RE.match(entry.blocked_on or "")


def _todo_links(
    todo: Todo, urls: list[str], states: dict[str, PrState], graph: Graph
) -> list[Link]:
    """The entry's PR links, with the ones the todo mentions first and highlighted, then the
    PRs and Jira tickets only the todo is about (named in it, or linked with its Link button).

    A todo mentions a PR by its URL, `#<number>`, or the bare number (PR numbers are long
    enough that a matching bare number in a todo is almost surely the PR).
    """
    text = todo.text
    own_prs = [*PR_URL_RE.findall(text), *(link.ref for link in todo.links if link.kind == PR)]
    own_keys = [
        *(r.id for r in graph.named(text) if r.kind == TICKET),
        *(link.ref for link in todo.links if link.kind == TICKET),
    ]
    links: list[Link] = []
    for u in urls:
        number = u.rsplit("/", 1)[1]
        mentioned = u in own_prs or re.search(rf"(?<![\w/])#?{number}\b", text) is not None
        links.append(Link(text=f"PR {number}", url=u, strong=mentioned, pr_state=states.get(u)))
    links.sort(key=lambda link: not link.strong)
    for u in dict.fromkeys(own_prs):
        if u not in urls:
            number = u.rsplit("/", 1)[1]
            links.append(Link(text=f"PR {number}", url=u, strong=True, pr_state=states.get(u)))
    for key in dict.fromkeys(own_keys):
        if url := graph.url_for(TICKET, key):
            links.append(Link(text=key, url=url, strong=True))
    return links


def _todo_part(
    entry: str,
    todo: Todo,
    urls: list[str],
    states: dict[str, PrState],
    badges: list[str],
    graph: Graph,
) -> Item:
    """One todo as focus mode shows it, linked to the PR it mentions if any.

    When every PR it mentions is merged or closed, the todo is probably done: it says so and
    its Done box is drawn bigger.
    """
    links = _todo_links(todo, urls, states, graph)
    mentioned = [link for link in links if link.strong]
    known = [link.pr_state for link in mentioned if link.pr_state is not None]
    probably_done = bool(known) and all(state in ("merged", "closed") for state in known)
    return Item(
        title=todo.text,
        url=mentioned[0].url if mentioned else None,
        subtitle=entry,
        badges=[todo.size or "unsized", *(["its PRs are done"] if probably_done else []), *badges],
        links=links,
        actions=todo_actions(todo, entry, suggested=probably_done),
    )


def _entry_urls(entry: Entry) -> list[str]:
    return sorted(link.ref for link in entry.links if link.kind == PR)


def entry_keys(entry: Entry, graph: Graph) -> list[str]:
    """The tickets an entry is about: linked to it, or named in its title."""
    named = [r.id for r in graph.named(entry.title) if r.kind == TICKET]
    return sorted({*named, *(link.ref for link in entry.links if link.kind == TICKET)})


def entities(entries: list[Entry], ices: list[Ice]) -> list[Entity]:
    """`entry` per work entry, pointing at the PRs and tickets it and its todos are linked to
    and mentioning its title (for the tickets it names), and `iced_branch` per branch on
    ice."""
    result: list[Entity] = []
    for entry in entries:
        links = [*entry.links, *(link for todo in entry.open_todos for link in todo.links)]
        refs = list(dict.fromkeys(Ref(kind=link.kind, id=link.ref) for link in links))
        result.append(
            Entity.model_validate(
                {
                    "ref": Ref(kind=ENTRY, id=entry.title),
                    "title": entry.title,
                    "refs": refs,
                    "mentions": [entry.title],
                    "attrs": attrs(
                        EntryAttrs(
                            merge_conditions=entry.conditions,
                            open_todos=[t.text for t in entry.open_todos],
                        )
                    ),
                }
            )
        )
    for ice in ices:
        for item in ice.items:
            if item.kind != BRANCH:
                continue
            repo_dir, _, name = item.ref_id.partition(BRANCH_ID_SEPARATOR)
            result.append(
                Entity(
                    ref=Ref(kind=ICED_BRANCH, id=item.ref_id),
                    title=name,
                    attrs=attrs(
                        IcedBranchAttrs(repo_dir=repo_dir, name=name, stack=ice.title, sha=item.sha)
                    ),
                )
            )
    return result


class _PrView(Model):
    """What notes reads of a `pr` (github's)."""

    id: str
    title: str
    state: str | None = None
    mine: bool


class _BranchView(Model):
    """What notes reads of a `branch` (git's); a branch only git-stack still tracks has
    neither."""

    id: str
    repo_dir: str | None = None
    name: str | None = None


class _TicketView(Model):
    """What notes reads of a `ticket` (jira's)."""

    id: str
    url: str | None = None
    summary: str | None = None


class Builder:
    def __init__(
        self,
        now: datetime,
        graph: Graph,
        entries: list[Entry],
        ices: list[Ice],
    ):
        self.now = now
        self.graph = graph
        self.entries = entries
        self.ices = ices

    def add_todo(self, item: Item) -> Action | None:
        """An Add todo field for an item about one of my open PRs or a ticket.

        The todo goes to the entry covering the PR or ticket; without one, an entry is created,
        titled with the ticket key and summary (or the PR's URL and title, as the skill does).
        """
        ref = item.subject(PR) or item.subject(TICKET)
        if any(a.name == TODO_ADD for a in item.actions) or ref is None:
            return None
        if ref.kind == PR:
            pr = self.graph.view_one(PR, ref.id, _PrView)
            entity = self.graph.get(PR, ref.id)
            if pr is None or entity is None or not (pr.mine and pr.state == "open"):
                return None
            keys = [r.id for r in entity.refs if r.kind == TICKET]
            entry = entry_for(self.entries, pr.id, keys, self.graph)
            ticket = self.graph.view_one(TICKET, keys[0], _TicketView) if keys else None
            summary = ticket.summary if ticket else None
            title = f"{keys[0]} {summary or pr.title}" if keys else f"{pr.id} {pr.title}"
            return add_todo_action(entry, title, pr.id)
        ticket = self.graph.view_one(TICKET, ref.id, _TicketView)
        if ticket is None:
            return None
        entry = entry_for(self.entries, ticket.url, [ticket.id], self.graph)
        title = f"{ticket.id} {ticket.summary}" if ticket.summary else ticket.id
        return add_todo_action(entry, title, ticket.url or "")

    def put_on_ice(self, item: Item) -> Action | None:
        """A Put on ice button for an item about one of git's branches, unless it's on ice."""
        ref = item.subject(BRANCH)
        branch = self.graph.view_one(BRANCH, ref.id, _BranchView) if ref else None
        if branch is None or branch.repo_dir is None or branch.name is None:
            return None  # not one of git's
        if self.graph.get(ICED_BRANCH, branch.id) is not None:
            return None
        return Action(
            name="notes/ice",
            label="Put on ice",
            fields={"repo": branch.repo_dir, "branch": branch.name},
        )

    def _pr_states(self) -> dict[str, PrState]:
        return {
            pr.id: PR_STATES[pr.state]
            for pr in self.graph.view(PR, _PrView)
            if pr.state in PR_STATES
        }

    def kept_todo(self, todo_id: int) -> Item | None:
        """A todo just ticked off (or back on), for the focus page to keep showing with its box
        as it is now, so the tick can be undone; None if it's gone."""
        for entry in self.entries:
            for todo in entry.todos:
                if todo.id == todo_id:
                    links = _todo_links(todo, _entry_urls(entry), self._pr_states(), self.graph)
                    mentioned = [link for link in links if link.strong]
                    return Item(
                        title=todo.text,
                        url=(mentioned or links)[0].url if links else None,
                        subtitle=entry.title,
                        badges=["done"] if todo.done_on is not None else [todo.size or "unsized"],
                        links=links,
                        actions=todo_actions(todo, entry.title),
                    )
        return None

    def todos(self) -> Panel:
        items: list[Item] = []
        states = self._pr_states()
        for entry in self.entries:
            open_todos = entry.open_todos
            if not open_todos:
                continue
            urls = _entry_urls(entry)
            keys = entry_keys(entry, self.graph)
            blocked = _is_blocked(entry)
            quick = [t for t in open_todos if t.size == "quick"]
            close_out = len(quick) == len(open_todos)
            badges: list[str] = []
            if close_out:
                badges.append(f"close-out: {len(open_todos)} quick todo(s)")
            if blocked:
                badges.append("blocked: " + (entry.blocked_on or "")[:80])
            url = urls[0] if urls else None
            links = [Link(text=f"PR {u.rsplit('/', 1)[1]}", url=u) for u in urls]
            ordered = sorted(open_todos, key=lambda t: t.size != "quick")
            items.append(
                Item(
                    title=entry.title,
                    url=url,
                    subtitle=", ".join(keys),
                    badges=badges,
                    details=[f"[{t.size or '?'}] {t.text}" for t in ordered],
                    links=links,
                    rank=(0 if blocked else 100) + (50 if close_out else 0) + len(quick),
                    actions=[add_todo_action(entry.title)],
                    parts=[
                        _todo_part(
                            entry.title,
                            t,
                            urls,
                            states,
                            badges[1 if close_out else 0 :],
                            self.graph,
                        )
                        for t in ordered
                    ],
                )
            )
        return Panel(
            key="todos",
            title="Todos from the work notes",
            description="Easy close-outs first; blocked work last.",
            items=items,
        )

    def iced(self) -> Panel:
        items: list[Item] = []
        for ice in self.ices:
            branches = [item.ref_id.partition(BRANCH_ID_SEPARATOR) for item in ice.items]
            repo_dir = branches[0][0] if branches else "?"
            items.append(
                Item(
                    title=ice.title,
                    subtitle=f"{repo_dir} · iced {ice.iced_on.isoformat()}",
                    details=[ice.note] if ice.note else [],
                    badges=[f"{len(ice.items)} branches"],
                    actions=[
                        Action(name="notes/thaw", label="Reopen", fields={"ice": str(ice.id)})
                    ],
                    # git and git_stack add deleting and untracking the branches by these.
                    subjects=[
                        Ref(kind=ICED_BRANCH, id=item.ref_id)
                        for item in ice.items
                        if item.kind == BRANCH
                    ],
                )
            )
        return Panel(
            key="iced", title="On ice", description="Stacks parked by the skill.", items=items
        )
