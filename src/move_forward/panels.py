"""The panels that aren't a plugin's yet: the work notes' todos, stacks on ice, and review
nudges with their ping targets; plus the Dashboard that collects every panel.

Pure functions, no I/O. They read other plugins' data through the graph only; the notes and
the reviewer map come from core's tables until they become plugins of their own.
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, cast

from pydantic import Field

from .config import DEFAULT_PING_FIRST, DEFAULT_PING_FOLLOW_UP, SlackConfig
from .domain import IcedStack, Model, Ping, ReviewerUser, TeamChannel, Todo, WorkItem
from .graph import Entity, Graph, Ref
from .page import Action, Group, Item, Link, Panel, PrState, SourceStatus, age

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
NOT_BLOCKED_RE = re.compile(r"^\s*(nothing|none|no|-|n/a)\b", re.IGNORECASE)

FIRST_PING = DEFAULT_PING_FIRST
FOLLOW_UP_PING = DEFAULT_PING_FOLLOW_UP

PR = "pr"
TICKET = "ticket"
ENTRY = "entry"
ICED_BRANCH = "iced_branch"
PERSON = "person"
PR_STATES: dict[str, PrState] = {"open": "open", "merged": "merged", "closed": "closed"}


# Output models


class PingTarget(Model):
    kind: Literal["user", "team"]
    name: str
    """Login or team slug."""
    slack_link: str | None = None
    """Where to paste the message: the channel or the person's profile."""
    destination: str
    """What the link opens, e.g. `#team-web (C0123ABCD)` or `alice (DM)`."""
    ping_kind: Literal["first", "follow-up"]
    last_ping: datetime | None = None
    message: str


class PrPing(Model):
    """Everything the ping button needs for one of my PRs."""

    pr_url: str
    targets: list[PingTarget]
    blockers: list[str]
    """Why the skill wouldn't nudge yet; empty when the PR is in order."""
    stale: bool
    """No review activity for `stale_review_hours`."""


GROUPS = [
    Group(
        key="act",
        title="Act now",
        panel_keys=["reviews_for_me", "feedback", "ci_failures", "conflicts"],
        open=True,
    ),
    Group(
        key="my_prs",
        title="My PRs",
        panel_keys=["needs_reviewer", "nudges", "ready_to_merge", "held", "metadata"],
    ),
    Group(key="local", title="Local", panel_keys=["local_work", "stacks", "cleanup"]),
    Group(key="team", title="Team", panel_keys=["team_prs"]),
    Group(key="planning", title="Planning", panel_keys=["todos", "jira_transitions", "jira_next"]),
    Group(key="parked", title="Parked", panel_keys=["iced"]),
]
"""Page order. Every panel `build` makes belongs to exactly one group."""

FOCUS_ORDER = [
    "todos",  # 0b. Reconcile work notes and check in
    "reviews_for_me",  # 1. Reviews requested of me
    "conflicts",  # 2. Sync stacks
    "feedback",  # 3. Feedback on my PRs
    "ci_failures",  # 4. CI failures and merge-queue evictions
    "local_work",  # 5. Restack and push
    "stacks",  # 6. Stacks without PRs
    "metadata",  # 7. PR metadata
    "needs_reviewer",  # 8a. Replace team review requests with a person
    "team_prs",  # 8a, for the team's PRs
    "nudges",  # 8. Stale reviews and nudges
    "ready_to_merge",  # 9. Merge queue
    "held",  # 9, with merge conditions
    "cleanup",  # 10. Cleanup
    "jira_transitions",  # 12. Jira updates
    "jira_next",  # 13. What next
    "iced",  # parked; the skill leaves these alone
]
"""Panel keys in the skill's step order. Focus mode's "All" goes through them in turn."""
FOCUS_ALL = "all"
"""The focus category that covers every panel."""


def todo_actions(entry: str, text: str, done: bool, suggested: bool = False) -> list[Action]:
    """A todo's checkbox (done, or undone once it's done) and, while open, its delete button.

    `suggested` draws the Done box bigger, when every PR the todo is about is merged or closed.
    """
    fields = {"entry": entry, "text": text}
    if done:
        return [
            Action(name="todo_undone", label="Done", fields=fields, checkbox=True, checked=True)
        ]
    return [
        Action(name="todo_done", label="Done", fields=fields, checkbox=True, suggested=suggested),
        Action(
            name="todo_delete",
            label="Delete todo",
            fields=fields,
            confirm=f"Drop the todo “{text}” from {entry}? It isn't recorded as done.",
        ),
        Action(name="todo_link", label="Link", fields=fields, input="Jira key or PR URL"),
        add_todo_action(entry),
    ]


ADD_TODO_INPUT = "New todo"


def add_todo_action(entry: str | None, new_title: str = "", where: str = "") -> Action:
    """An Add todo field for a work-notes entry, or for one to create titled `new_title`."""
    if entry is not None:
        return Action(
            name="todo_add", label="Add todo", fields={"entry": entry}, input=ADD_TODO_INPUT
        )
    return Action(
        name="todo_add",
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


class Dashboard(Model):
    generated_at: datetime
    login: str
    panels: list[Panel]
    groups: list[Group] = Field(default_factory=lambda: list(GROUPS))

    def panel(self, key: str) -> Panel:
        return next(p for p in self.panels if p.key == key)

    def find_panel(self, key: str) -> Panel | None:
        return next((p for p in self.panels if p.key == key), None)

    def find_group(self, key: str) -> Group | None:
        return next((g for g in self.groups if g.key == key), None)

    def focus_title(self, category: str) -> str | None:
        """What the page calls a focus category; None for an unknown one.

        A category is `FOCUS_ALL`, a group key or a panel key.
        """
        if category == FOCUS_ALL:
            return "All"
        if (group := self.find_group(category)) is not None:
            return group.title
        if (panel := self.find_panel(category)) is not None:
            return panel.title
        return None

    def focus_items(self, category: str) -> list[tuple[Panel, Item]] | None:
        """The items focus mode steps through for a category, each with its panel.

        Panels come in `FOCUS_ORDER`, items in their panel's order, and an item with `parts`
        contributes those instead of itself; None for an unknown category.
        """
        if category == FOCUS_ALL:
            keys = FOCUS_ORDER
        elif (group := self.find_group(category)) is not None:
            keys = [key for key in FOCUS_ORDER if key in group.panel_keys]
        elif self.find_panel(category) is not None:
            keys = [category]
        else:
            return None
        panels = [p for key in keys if (p := self.find_panel(key))]
        return [
            (panel, part)
            for panel in panels
            for item in panel.items
            for part in item.parts or [item]
        ]

    def done_todo(self, entry: str, text: str) -> Item:
        """A todo just marked done, for the focus page to keep showing with its box ticked.

        Done todos aren't in the panels, so this is rebuilt from the action's fields; the
        entry's PR links come along if the entry still has open todos.
        """
        todos = self.find_panel("todos")
        parent = next((i for i in todos.items if i.title == entry), None) if todos else None
        return Item(
            title=text,
            url=parent.url if parent else None,
            subtitle=entry,
            badges=["done"],
            links=parent.links if parent else [],
            actions=todo_actions(entry, text, done=True),
        )

    pings: dict[str, PrPing]
    sources: dict[str, SourceStatus]


@dataclass(frozen=True)
class Inputs:
    """Everything `build` reads besides the plugins' panels, so it stays a pure function."""

    now: datetime
    graph: Graph
    work_items: list[WorkItem] = field(default_factory=list[WorkItem])
    iced_stacks: list[IcedStack] = field(default_factory=list[IcedStack])
    users: dict[str, ReviewerUser] = field(default_factory=dict[str, ReviewerUser])
    teams: dict[str, TeamChannel] = field(default_factory=dict[str, TeamChannel])
    """Team name to its Slack channel."""
    pings: list[Ping] = field(default_factory=list[Ping])
    slack: SlackConfig = field(default_factory=SlackConfig)


# Helpers


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


def _slack_link(workspace: str, path: str) -> str | None:
    return f"{workspace.rstrip('/')}/{path}" if workspace else None


def _datetime(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _strings(value: object) -> list[str]:
    return [str(v) for v in cast(list[object], value)] if isinstance(value, list) else []


def entities(
    work_items: list[WorkItem], iced_stacks: list[IcedStack], users: dict[str, ReviewerUser]
) -> list[Entity]:
    """What the notes and the reviewer map publish to the graph: `entry` per work-notes entry,
    `iced_branch` per branch on ice, and `person` per known reviewer."""
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
    for user in users.values():
        result.append(
            Entity(
                ref=Ref(kind=PERSON, id=user.login),
                title=user.login,
                attrs={"email": user.email, "slack_id": user.slack_id},
            )
        )
    return result


# Panels


class _Builder:
    def __init__(self, inputs: Inputs):
        self.i = inputs
        self.now = inputs.now
        self.graph = inputs.graph
        self.my_open_prs = [
            pr for pr in self.graph.of_kind(PR) if pr.attrs.get("mine") and pr.state == "open"
        ]

    def _iced(self, pr: Entity) -> bool:
        return self.graph.get(ICED_BRANCH, str(pr.attrs.get("head"))) is not None

    # Nudges

    def _ping_targets(self, pr: Entity, last_by_target: dict[str, Ping]) -> list[PingTarget]:
        targets: list[PingTarget] = []
        workspace = self.i.slack.workspace_url
        for login in _strings(pr.attrs.get("requested_users")):
            user = self.i.users.get(login)
            link = (
                _slack_link(workspace, f"team/{user.slack_id}") if user and user.slack_id else None
            )
            targets.append(
                self._target(
                    "user", login, link, f"{login} (DM)", last_by_target.get(f"user:{login}"), pr
                )
            )
        for name in _strings(pr.attrs.get("requested_teams")):
            team = self.i.teams.get(name)
            link = None
            destination = f"team {name}"
            if team and team.channel_id:
                link = _slack_link(workspace, f"archives/{team.channel_id}")
                destination = f"{team.channel_name or name} ({team.channel_id})"
            elif team and team.channel_name:
                destination = team.channel_name
            targets.append(
                self._target(
                    "team", name, link, destination, last_by_target.get(f"team:{name}"), pr
                )
            )
        return targets

    def _target(
        self,
        kind: Literal["user", "team"],
        name: str,
        link: str | None,
        destination: str,
        last: Ping | None,
        pr: Entity,
    ) -> PingTarget:
        ping_kind: Literal["first", "follow-up"] = "follow-up" if last else "first"
        return PingTarget(
            kind=kind,
            name=name,
            slack_link=link,
            destination=destination,
            ping_kind=ping_kind,
            last_ping=last.sent_at if last else None,
            message=self.i.slack.ping_first.format(url=pr.ref.id)
            if ping_kind == "first"
            else self.i.slack.ping_follow_up,
        )

    def pings(self) -> dict[str, PrPing]:
        by_pr: dict[str, dict[str, Ping]] = defaultdict(dict)
        any_ping: dict[str, Ping] = {}
        for ping in self.i.pings:
            by_pr[ping.pr_url][ping.target] = ping
            any_ping[ping.pr_url] = ping
        result: dict[str, PrPing] = {}
        for pr in self.my_open_prs:
            if pr.attrs.get("draft") or self._iced(pr):
                continue
            url = pr.ref.id
            last_by_target = dict(by_pr.get(url, {}))
            # A nudge the skill logged without a target counts for every target.
            if (untargeted := any_ping.get(url)) and untargeted.target.startswith("unknown"):
                for login in _strings(pr.attrs.get("requested_users")):
                    last_by_target.setdefault(f"user:{login}", untargeted)
                for name in _strings(pr.attrs.get("requested_teams")):
                    last_by_target.setdefault(f"team:{name}", untargeted)
            result[url] = PrPing(
                pr_url=url,
                targets=self._ping_targets(pr, last_by_target),
                blockers=_strings(pr.attrs.get("review_blockers")),
                stale=bool(pr.attrs.get("review_stale")),
            )
        return result

    def nudges(self, pings: dict[str, PrPing]) -> Panel:
        items: list[Item] = []
        for pr in self.my_open_prs:
            ping = pings.get(pr.ref.id)
            if not ping or ping.blockers or not ping.stale:
                continue
            details = [
                f"{t.ping_kind} ping to {t.destination}"
                + (f" (last {age(self.now, t.last_ping)} ago)" if t.last_ping else "")
                for t in ping.targets
            ]
            last = _datetime(pr.attrs.get("last_review_activity"))
            details.append(f"last review activity {age(self.now, last)} ago")
            since = last or _datetime(pr.attrs.get("created_at")) or self.now
            items.append(
                Item(
                    title=pr.title,
                    url=pr.url,
                    subtitle=f"{pr.attrs.get('repo')}#{pr.attrs.get('number')}",
                    details=details,
                    rank=-since.timestamp(),
                    subject=pr.ref,
                )
            )
        return Panel(
            key="nudges",
            title="Nudge reviewers",
            description="In order (green, no conflicts, no open threads) and quiet for a day.",
            items=items,
        )

    # Notes

    def with_add_todo(self, panel: Panel) -> Panel:
        """Give each of my open PRs, each ticket and each work-notes entry an Add todo field.

        The todo goes to the entry covering the PR or ticket; without one, an entry is created,
        titled with the ticket key and summary (or the PR's URL and title, as the skill does).
        """
        entries = {item.title for item in self.i.work_items}
        items: list[Item] = []
        for item in panel.items:
            action: Action | None = None
            subject = self.graph.get(item.subject.kind, item.subject.id) if item.subject else None
            if any(a.name == "todo_add" for a in item.actions):
                pass
            elif panel.key == "todos" and item.title in entries:
                action = add_todo_action(item.title)
            elif subject and subject.ref.kind == PR:
                if subject.attrs.get("mine") and subject.state == "open":
                    keys = [r.id for r in subject.refs if r.kind == TICKET]
                    entry = entry_for(self.i.work_items, subject.ref.id, keys)
                    ticket = self.graph.get(TICKET, keys[0]) if keys else None
                    summary = ticket.attrs.get("summary") if ticket else None
                    title = (
                        f"{keys[0]} {summary or subject.title}"
                        if keys
                        else f"{subject.ref.id} {subject.title}"
                    )
                    action = add_todo_action(entry, title, subject.ref.id)
            elif subject and subject.ref.kind == TICKET:
                key = subject.ref.id
                entry = entry_for(self.i.work_items, subject.url, [key])
                summary = subject.attrs.get("summary")
                action = add_todo_action(
                    entry, f"{key} {summary}" if summary else key, subject.url or ""
                )
            items.append(
                item.model_copy(update={"actions": [*item.actions, action]}) if action else item
            )
        return panel.model_copy(update={"items": items})

    def _pr_states(self) -> dict[str, PrState]:
        return {
            pr.ref.id: PR_STATES[pr.state] for pr in self.graph.of_kind(PR) if pr.state in PR_STATES
        }

    def todos(self) -> Panel:
        items: list[Item] = []
        states = self._pr_states()
        for item in self.i.work_items:
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
                    Action(name="thaw", label="Reopen", fields={"branch": stack.bottom_branch}),
                    Action(
                        name="delete_iced",
                        label="Delete the work",
                        fields={"branch": stack.bottom_branch},
                        confirm=f"Delete the local branches {', '.join(b.name for b in stack.branches)}"
                        f" in {stack.repo_dir} and drop the stack from on-ice.md? Remote branches"
                        " and PRs are not touched.",
                    ),
                ],
            )
            for stack in self.i.iced_stacks
        ]
        return Panel(
            key="iced", title="On ice", description="Stacks parked by the skill.", items=items
        )


def _sorted(panel: Panel) -> Panel:
    """Highest rank first, drafts last."""
    return panel.model_copy(update={"items": sorted(panel.items, key=lambda i: (i.draft, -i.rank))})


ItemActions = Callable[[Item], list[Action]]
"""The buttons the plugins add to an item, whoever built it; see `Plugin.item_actions`."""


def _no_item_actions(item: Item) -> list[Action]:
    return []


def _with_item_actions(panel: Panel, item_actions: ItemActions) -> Panel:
    """Each item, and each of its focus-mode parts, with the plugins' buttons added."""

    def add(item: Item) -> Item:
        return item.model_copy(
            update={
                "actions": [*item.actions, *item_actions(item)],
                "parts": [add(part) for part in item.parts],
            }
        )

    return panel.model_copy(update={"items": [add(item) for item in panel.items]})


def build(
    inputs: Inputs,
    plugin_panels: list[Panel],
    sources: dict[str, SourceStatus],
    login: str = "",
    item_actions: ItemActions = _no_item_actions,
) -> Dashboard:
    """The dashboard: the plugins' panels and these, each with its Add todo fields and the
    plugins' item buttons, sorted."""
    b = _Builder(inputs)
    pings = b.pings()
    panels = [*plugin_panels, b.nudges(pings), b.todos(), b.iced()]
    return Dashboard(
        generated_at=inputs.now,
        login=login,
        panels=[_sorted(_with_item_actions(b.with_add_todo(p), item_actions)) for p in panels],
        pings=pings,
        sources=sources,
    )
