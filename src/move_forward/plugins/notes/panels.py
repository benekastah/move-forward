"""The notes plugin's panels: todos from the work notes and stacks on ice, and what it puts on
other plugins' items: Add todo on items about my PRs and tickets, Put on ice on items about a
branch. Pure functions; no I/O."""

import re
from datetime import datetime

from ...domain import IcedStack, Todo, WorkItem
from ...graph import Entity, Graph, Ref
from ...page import Action, Item, Link, Panel, PrState

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
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


def entry_for(work_items: list[WorkItem], url: str | None, keys: list[str]) -> str | None:
    """The work-notes entry that covers a PR or ticket: one that names the URL, else one whose
    title has one of the ticket keys."""
    if url:
        for item in work_items:
            if url in item.text:
                return item.title
    for key in keys:
        for item in work_items:
            if key in _item_keys(item):
                return item.title
    return None


def _is_blocked(item: WorkItem) -> bool:
    values = item.fields.get("Blocked on", [])
    return bool(values) and not NOT_BLOCKED_RE.match(values[0])


def _todo_links(text: str, urls: list[str], states: dict[str, PrState], graph: Graph) -> list[Link]:
    """The entry's PR links, with the ones the todo mentions first and highlighted, then the
    PRs and Jira tickets only the todo mentions (e.g. linked with its Link button).

    A todo mentions a PR by its URL, `#<number>`, or the bare number (PR numbers are long
    enough that a matching bare number in a todo is almost surely the PR).
    """
    links: list[Link] = []
    for u in urls:
        number = u.rsplit("/", 1)[1]
        mentioned = u in text or re.search(rf"(?<![\w/])#?{number}\b", text) is not None
        links.append(Link(text=f"PR {number}", url=u, strong=mentioned, pr_state=states.get(u)))
    links.sort(key=lambda link: not link.strong)
    for u in PR_URL_RE.findall(text):
        if u not in urls:
            number = u.rsplit("/", 1)[1]
            links.append(Link(text=f"PR {number}", url=u, strong=True, pr_state=states.get(u)))
    for key in dict.fromkeys(JIRA_KEY_RE.findall(text)):
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
    links = _todo_links(todo.text, urls, states, graph)
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


def _item_urls(item: WorkItem) -> set[str]:
    return set(PR_URL_RE.findall(item.text))


def _item_keys(item: WorkItem) -> set[str]:
    return set(JIRA_KEY_RE.findall(item.title))


def merge_conditions(item: WorkItem) -> list[str]:
    return item.fields.get("Merge conditions", [])


def entities(work_items: list[WorkItem], iced_stacks: list[IcedStack]) -> list[Entity]:
    """`entry` per work-notes entry and `iced_branch` per branch on ice."""
    result: list[Entity] = []
    for item in work_items:
        refs = [Ref(kind=PR, id=url) for url in sorted(_item_urls(item))]
        refs += [Ref(kind=TICKET, id=key) for key in sorted(_item_keys(item))]
        result.append(
            Entity.model_validate(
                {
                    "ref": Ref(kind=ENTRY, id=item.title),
                    "title": item.title,
                    "refs": refs,
                    "attrs": {
                        "merge_conditions": merge_conditions(item),
                        "open_todos": [t.text for t in item.todos],
                    },
                }
            )
        )
    for stack in iced_stacks:
        for branch in stack.branches:
            result.append(
                Entity(
                    ref=Ref(kind=ICED_BRANCH, id=branch.name),
                    title=branch.name,
                    attrs={"repo_dir": stack.repo_dir, "stack": stack.bottom_branch},
                )
            )
    return result


class Builder:
    def __init__(
        self,
        now: datetime,
        graph: Graph,
        work_items: list[WorkItem],
        iced_stacks: list[IcedStack],
    ):
        self.now = now
        self.graph = graph
        self.work_items = work_items
        self.iced_stacks = iced_stacks

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
            entry = entry_for(self.work_items, subject.ref.id, keys)
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
            entry = entry_for(self.work_items, subject.url, [key])
            summary = subject.attrs.get("summary")
            return add_todo_action(entry, f"{key} {summary}" if summary else key, subject.url or "")
        return None

    def put_on_ice(self, item: Item) -> Action | None:
        """A Put on ice button for an item about one of git's branches, unless it's on ice."""
        ref = item.subject(BRANCH)
        if ref is None or self.graph.get(BRANCH, ref.id) is None:
            return None
        repo_dir, _, name = ref.id.partition(BRANCH_ID_SEPARATOR)
        if self.graph.get(ICED_BRANCH, name) is not None:
            return None
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
        for item in self.work_items:
            open_todos = item.todos
            if not open_todos:
                continue
            urls = sorted(_item_urls(item))
            keys = sorted(_item_keys(item))
            blocked = _is_blocked(item)
            quick = [t for t in open_todos if t.size == "quick"]
            close_out = len(quick) == len(open_todos)
            badges: list[str] = []
            if close_out:
                badges.append(f"close-out: {len(open_todos)} quick todo(s)")
            if blocked:
                badges.append("blocked: " + item.fields["Blocked on"][0][:80])
            url = urls[0] if urls else None
            links = [Link(text=f"PR {u.rsplit('/', 1)[1]}", url=u) for u in urls]
            ordered = sorted(open_todos, key=lambda t: t.size != "quick")
            items.append(
                Item(
                    title=item.title,
                    url=url,
                    subtitle=", ".join(keys),
                    badges=badges,
                    details=[f"[{t.size or '?'}] {t.text}" for t in ordered],
                    links=links,
                    rank=(0 if blocked else 100) + (50 if close_out else 0) + len(quick),
                    actions=[add_todo_action(item.title)],
                    parts=[
                        _todo_part(
                            item.title,
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
        items = [
            Item(
                title=stack.bottom_branch,
                subtitle=f"{stack.repo_dir} · iced {stack.iced or '?'}",
                details=[stack.note] if stack.note else [],
                badges=[f"{len(stack.branches)} branches"],
                actions=[
                    Action(
                        name="notes/thaw", label="Reopen", fields={"branch": stack.bottom_branch}
                    ),
                    Action(
                        name="notes/delete_iced",
                        label="Delete the work",
                        fields={"branch": stack.bottom_branch},
                        confirm=f"Delete the local branches {', '.join(b.name for b in stack.branches)}"
                        f" in {stack.repo_dir} and drop the stack from on-ice.md? Remote branches"
                        " and PRs are not touched.",
                    ),
                ],
            )
            for stack in self.iced_stacks
        ]
        return Panel(
            key="iced", title="On ice", description="Stacks parked by the skill.", items=items
        )
