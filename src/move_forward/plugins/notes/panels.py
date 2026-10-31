"""The notes plugin's panels: todos from the work notes and stacks on ice, and what it puts on
other plugins' items: Add todo on items about my PRs and tickets, Put on ice on items about a
branch. Pure functions; no I/O."""

import re
from datetime import datetime

from ...graph import Entity, Graph, Ref
from ...page import Action, Item, Link, Panel, PrState
from .markdown import JIRA_KEY_RE, PR_URL_RE
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


def todo_actions(entry: str, text: str, done: bool, suggested: bool = False) -> list[Action]:
    """A todo's checkbox (done, or undone once it's done) and, while open, its delete button.

    `suggested` draws the Done box bigger, when every PR the todo is about is merged or closed.
    """
    fields = {"entry": entry, "text": text}
    if done:
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
            confirm=f"Drop the todo “{text}” from {entry}? It isn't recorded as done.",
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


def entry_for(entries: list[Entry], url: str | None, keys: list[str]) -> str | None:
    """The entry that covers a PR or ticket: one that names the URL, else one whose title has
    one of the ticket keys."""
    if url:
        for entry in entries:
            if url in _entry_urls(entry):
                return entry.title
    for key in keys:
        for entry in entries:
            if key in _entry_keys(entry):
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
        *JIRA_KEY_RE.findall(text),
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
        actions=todo_actions(entry, todo.text, done=False, suggested=probably_done),
    )


def _entry_urls(entry: Entry) -> list[str]:
    return sorted(link.ref for link in entry.links if link.kind == PR)


def _entry_keys(entry: Entry) -> list[str]:
    return sorted(link.ref for link in entry.links if link.kind == TICKET)


def entities(entries: list[Entry], ices: list[Ice]) -> list[Entity]:
    """`entry` per work entry, pointing at the PRs and tickets it and its todos are about, and
    `iced_branch` per branch on ice."""
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
                    "attrs": {
                        "merge_conditions": entry.conditions,
                        "open_todos": [t.text for t in entry.open_todos],
                    },
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
                    attrs={"repo_dir": repo_dir, "name": name, "stack": ice.title},
                )
            )
    return result


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
        subject = self.graph.get(ref.kind, ref.id)
        if subject is None:
            return None
        if subject.ref.kind == PR:
            if not (subject.attrs.get("mine") and subject.state == "open"):
                return None
            keys = [r.id for r in subject.refs if r.kind == TICKET]
            entry = entry_for(self.entries, subject.ref.id, keys)
            ticket = self.graph.get(TICKET, keys[0]) if keys else None
            summary = ticket.attrs.get("summary") if ticket else None
            title = (
                f"{keys[0]} {summary or subject.title}"
                if keys
                else f"{subject.ref.id} {subject.title}"
            )
            return add_todo_action(entry, title, subject.ref.id)
        if subject.ref.kind == TICKET:
            key = subject.ref.id
            entry = entry_for(self.entries, subject.url, [key])
            summary = subject.attrs.get("summary")
            return add_todo_action(entry, f"{key} {summary}" if summary else key, subject.url or "")
        return None

    def put_on_ice(self, item: Item) -> Action | None:
        """A Put on ice button for an item about one of git's branches, unless it's on ice."""
        ref = item.subject(BRANCH)
        if ref is None or self.graph.get(BRANCH, ref.id) is None:
            return None
        if self.graph.get(ICED_BRANCH, ref.id) is not None:
            return None
        repo_dir, _, name = ref.id.partition(BRANCH_ID_SEPARATOR)
        return Action(
            name="notes/ice", label="Put on ice", fields={"repo": repo_dir, "branch": name}
        )

    def _pr_states(self) -> dict[str, PrState]:
        return {
            pr.ref.id: PR_STATES[pr.state] for pr in self.graph.of_kind(PR) if pr.state in PR_STATES
        }

    def todos(self) -> Panel:
        items: list[Item] = []
        states = self._pr_states()
        for entry in self.entries:
            open_todos = entry.open_todos
            if not open_todos:
                continue
            urls = _entry_urls(entry)
            keys = _entry_keys(entry)
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
            names = ", ".join(name for _, _, name in branches)
            fields = {"ice": str(ice.id)}
            items.append(
                Item(
                    title=ice.title,
                    subtitle=f"{repo_dir} · iced {ice.iced_on.isoformat()}",
                    details=[ice.note] if ice.note else [],
                    badges=[f"{len(ice.items)} branches"],
                    actions=[
                        Action(name="notes/thaw", label="Reopen", fields=fields),
                        Action(
                            name="notes/delete_iced",
                            label="Delete the work",
                            fields=fields,
                            confirm=f"Delete the local branches {names} in {repo_dir} and take"
                            " them off ice? Remote branches and PRs are not touched.",
                        ),
                    ],
                )
            )
        return Panel(
            key="iced", title="On ice", description="Stacks parked by the skill.", items=items
        )
