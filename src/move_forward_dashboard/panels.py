"""Turns the stored snapshots into the panels the page shows. Pure functions, no I/O."""

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field

from .config import (
    DEFAULT_BUG_TYPES,
    DEFAULT_INCIDENT_TYPES,
    DEFAULT_PING_FIRST,
    DEFAULT_PING_FOLLOW_UP,
    DEFAULT_PLACEHOLDER_LABELS,
    DEFAULT_STATUS_CLOSED,
    DEFAULT_STATUS_IN_PROGRESS,
    DEFAULT_STATUS_IN_REVIEW,
)
from .domain import (
    Check,
    DerivedSnapshot,
    GitHubSnapshot,
    IcedStack,
    JiraIssue,
    JiraSnapshot,
    LocalSnapshot,
    Model,
    Ping,
    PullRequest,
    ReviewerUser,
    TeamChannel,
    Thread,
    Todo,
    WorkItem,
)
from .page import Action, Group, Item, Link, Panel, PrState, SourceStatus

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
BOT_LOGIN_RE = re.compile(r"bot\b|\[bot\]$|^copilot", re.IGNORECASE)
NOT_BLOCKED_RE = re.compile(r"^\s*(nothing|none|no|-|n/a)\b", re.IGNORECASE)

FAILING_CHECK_STATES = frozenset(
    {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
)
PASSING_CHECK_STATES = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
MERGE_QUEUE_LOOKBACK = timedelta(days=2)

FIRST_PING = DEFAULT_PING_FIRST
FOLLOW_UP_PING = DEFAULT_PING_FOLLOW_UP

TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch in a bulk action's `targets` field, one per line."""


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


class JiraStatuses(Model):
    """The workflow status names the Jira panel reasons about; see `JiraConfig`."""

    in_progress: str = DEFAULT_STATUS_IN_PROGRESS
    in_review: str = DEFAULT_STATUS_IN_REVIEW
    closed: str = DEFAULT_STATUS_CLOSED


class Inputs(Model):
    """Everything `build` reads, so it stays a pure function."""

    now: datetime
    login: str
    my_teams: list[str] = Field(default_factory=list)
    """My GitHub team slugs; see `Config.teams`. `teams` below maps slugs to Slack channels."""
    github: GitHubSnapshot | None = None
    local: LocalSnapshot | None = None
    jira: JiraSnapshot | None = None
    derived: DerivedSnapshot | None = None
    work_items: list[WorkItem] = Field(default_factory=list)
    iced_stacks: list[IcedStack] = Field(default_factory=list)
    users: dict[str, ReviewerUser] = Field(default_factory=dict)
    teams: dict[str, TeamChannel] = Field(default_factory=dict)
    pings: list[Ping] = Field(default_factory=list)
    sources: dict[str, SourceStatus] = Field(default_factory=dict)
    slack_workspace_url: str = ""
    jira_site: str = ""
    """The Jira site URL, for linking ticket keys in todos; empty without Jira config."""
    stale_review_hours: int = 24
    review_checks: list[str] = Field(default_factory=list)
    my_branch_prefixes: list[str] = Field(default_factory=list)
    ping_first: str = DEFAULT_PING_FIRST
    ping_follow_up: str = DEFAULT_PING_FOLLOW_UP
    placeholder_labels: list[str] = Field(default_factory=lambda: list(DEFAULT_PLACEHOLDER_LABELS))
    ticket_link_prefix: str = ""
    """See `Config.ticket_link_prefix`; empty turns the missing-link check off."""
    jira_statuses: JiraStatuses = Field(default_factory=JiraStatuses)
    escalation_label_prefix: str = ""
    escalation_levels: list[str] = Field(default_factory=list)
    """See `JiraConfig.escalation_label_prefix` and `escalation_levels`."""
    incident_types: list[str] = Field(default_factory=lambda: list(DEFAULT_INCIDENT_TYPES))
    bug_types: list[str] = Field(default_factory=lambda: list(DEFAULT_BUG_TYPES))


# Helpers


def age(now: datetime, then: datetime | None) -> str:
    if then is None:
        return "?"
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    seconds = (now - then).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _subtitle(now: datetime, pr: PullRequest) -> str:
    parts = [f"{pr.repo}#{pr.number}", pr.author or "?", f"{age(now, pr.created_at)} old"]
    parts.append(f"updated {age(now, pr.updated_at)} ago")
    return " · ".join(parts)


def _badges(pr: PullRequest) -> list[str]:
    badges = [f"+{pr.size} lines", f"{pr.changed_files} files"]
    if pr.is_draft:
        badges.insert(0, "draft")
    return badges


def _item(
    now: datetime,
    pr: PullRequest,
    details: list[str] | None = None,
    links: list[Link] | None = None,
    rank: float = 0,
) -> Item:
    return Item(
        title=pr.title,
        url=pr.url,
        subtitle=_subtitle(now, pr),
        badges=_badges(pr),
        details=details or [],
        links=links or [],
        rank=rank,
        draft=pr.is_draft,
    )


def is_bot(login: str | None) -> bool:
    return login is None or bool(BOT_LOGIN_RE.search(login))


def approved(pr: PullRequest) -> bool:
    return pr.review_decision == "APPROVED" or any(r.state == "APPROVED" for r in pr.reviews)


def changes_requested(pr: PullRequest) -> bool:
    return pr.review_decision == "CHANGES_REQUESTED" or any(
        r.state == "CHANGES_REQUESTED" for r in pr.reviews
    )


def unresolved_threads(pr: PullRequest) -> list[Thread]:
    return [t for t in pr.threads if not t.resolved]


def failing_checks(pr: PullRequest) -> list[Check]:
    return [c for c in pr.checks if c.state in FAILING_CHECK_STATES]


def pending_checks(pr: PullRequest) -> list[Check]:
    return [c for c in pr.checks if c.state not in FAILING_CHECK_STATES | PASSING_CHECK_STATES]


def has_conflicts(pr: PullRequest) -> bool:
    return pr.mergeable == "CONFLICTING" or pr.merge_state == "DIRTY"


def _has_ticket_link_line(body: str, prefix: str) -> bool:
    """Whether a line of the PR body starts with `prefix` and names a ticket key."""
    pattern = rf"^\s*{re.escape(prefix)}\b.*?[A-Z][A-Z0-9]+-\d+"
    return re.search(pattern, body, re.IGNORECASE | re.MULTILINE) is not None


def ticket_keys(pr: PullRequest) -> list[str]:
    """Jira keys in the branch name, title or body, most specific source first."""
    seen: list[str] = []
    for text in (pr.head, pr.title, pr.body):
        for key in JIRA_KEY_RE.findall(text):
            if key not in seen:
                seen.append(key)
    return seen


def unanswered_feedback(pr: PullRequest, me: str) -> list[str]:
    """Review and comment events by others after my last push that I haven't followed up on."""
    since = pr.last_commit_at
    pending: list[str] = []
    for event in sorted(pr.events, key=lambda e: e.at):
        if event.type not in ("PullRequestReview", "IssueComment"):
            continue
        if since and event.at <= since:
            continue
        if event.actor == me:
            pending.clear()
        elif not is_bot(event.actor):
            pending.append(f"{event.type.removeprefix('PullRequest').lower()} by {event.actor}")
    return pending


def my_last_review(pr: PullRequest, me: str) -> datetime | None:
    mine = [r.submitted_at for r in pr.reviews if r.author == me and r.submitted_at]
    return max(mine) if mine else None


def needs_re_review(pr: PullRequest, me: str) -> str | None:
    """Why a PR I reviewed wants another look, or None."""
    last = my_last_review(pr, me)
    if last is None:
        return None
    if me in pr.requested_users:
        return "review re-requested"
    if pr.last_commit_at and pr.last_commit_at > last:
        return f"pushed {pr.last_commit_at.date()} after my review"
    return None


def last_review_activity(pr: PullRequest) -> datetime | None:
    """The latest review request, review or non-bot comment."""
    times = [
        e.at
        for e in pr.events
        if e.type == "ReviewRequestedEvent"
        or (e.type in ("PullRequestReview", "IssueComment") and not is_bot(e.actor))
    ]
    times += [r.submitted_at for r in pr.reviews if r.submitted_at]
    return max(times) if times else None


def _is_blocked(item: WorkItem) -> bool:
    values = item.fields.get("Blocked on", [])
    return bool(values) and not NOT_BLOCKED_RE.match(values[0])


def _todo_links(
    text: str, urls: list[str], states: dict[str, PrState], jira_site: str = ""
) -> list[Link]:
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
    if jira_site:
        for key in dict.fromkeys(JIRA_KEY_RE.findall(text)):
            links.append(Link(text=key, url=f"{jira_site.rstrip('/')}/browse/{key}", strong=True))
    return links


def _todo_part(
    entry: str,
    todo: Todo,
    urls: list[str],
    states: dict[str, PrState],
    badges: list[str],
    jira_site: str = "",
) -> Item:
    """One todo as focus mode shows it, linked to the PR it mentions if any.

    When every PR it mentions is merged or closed, the todo is probably done: it says so and
    its Done box is drawn bigger.
    """
    links = _todo_links(todo.text, urls, states, jira_site)
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


def _escalation(issue: JiraIssue, label_prefix: str, levels: list[str]) -> int:
    """How far an issue is escalated, from its `<prefix><level>` labels; 0 when it isn't.

    A level is its 1-based position in `levels`; one that isn't listed counts as 1.
    """
    if not label_prefix:
        return 0
    best = 0
    for label in issue.labels:
        if label.startswith(label_prefix):
            level = label[len(label_prefix) :]
            best = max(best, levels.index(level) + 1 if level in levels else 1)
    return best


def _expand(repo_dir: str) -> str:
    """A repo dir as on-ice.md writes it (`~/...`) in the form `LocalRepo.dir` uses."""
    return str(Path(repo_dir).expanduser())


def _ice_action(repo_dir: str, branch: str) -> Action:
    return Action(name="ice", label="Put on ice", fields={"repo": repo_dir, "branch": branch})


def _slack_link(workspace: str, path: str) -> str | None:
    return f"{workspace.rstrip('/')}/{path}" if workspace else None


# Panels


class _Builder:
    def __init__(self, inputs: Inputs):
        self.i = inputs
        self.now = inputs.now
        self.me = inputs.login
        gh = inputs.github
        self.mine: list[PullRequest] = gh.mine if gh else []
        self.open_by_branch: dict[tuple[str, str], PullRequest] = {
            (pr.repo, pr.head): pr for pr in self.mine
        }
        self.merged_by_branch: dict[tuple[str, str], PullRequest] = {
            (pr.repo, pr.head): pr for pr in (gh.merged_recent if gh else [])
        }
        self.iced_branches: set[str] = {
            b.name for stack in inputs.iced_stacks for b in stack.branches
        }
        self.items_by_url: dict[str, list[WorkItem]] = defaultdict(list)
        self.items_by_key: dict[str, list[WorkItem]] = defaultdict(list)
        for item in inputs.work_items:
            for url in _item_urls(item):
                self.items_by_url[url].append(item)
            for key in _item_keys(item):
                self.items_by_key[key].append(item)

    def _iced(self, pr: PullRequest) -> bool:
        return pr.head in self.iced_branches

    def _is_my_branch(self, repo: str | None, name: str) -> bool:
        """Mine by prefix (when prefixes are configured) or by having a PR of mine."""
        prefixes = self.i.my_branch_prefixes
        if not prefixes or any(name.startswith(p) for p in prefixes):
            return True
        key = (repo or "", name)
        return key in self.open_by_branch or key in self.merged_by_branch

    def _work_items_for(self, pr: PullRequest) -> list[WorkItem]:
        items = list(self.items_by_url.get(pr.url, []))
        for key in ticket_keys(pr):
            items += [i for i in self.items_by_key.get(key, []) if i not in items]
        return items

    def _held_by(self, pr: PullRequest) -> list[str]:
        return [c for item in self._work_items_for(pr) for c in merge_conditions(item)]

    # Reviews for me

    def reviews_for_me(self) -> Panel:
        gh = self.i.github
        items: list[Item] = []
        if gh:
            for pr in gh.direct_requests:
                items.append(
                    _item(
                        self.now, pr, details=["review requested"], rank=pr.updated_at.timestamp()
                    )
                )
            seen = {pr.url for pr in gh.direct_requests}
            for pr in gh.reviewed_by_me:
                reason = needs_re_review(pr, self.me)
                if reason and pr.url not in seen:
                    items.append(
                        _item(self.now, pr, details=[reason], rank=pr.updated_at.timestamp())
                    )
        return Panel(
            key="reviews_for_me",
            title="Reviews for me",
            description="PRs asking for my review directly, or that changed since I reviewed them.",
            items=items,
        )

    # My PRs

    def needs_reviewer(self) -> Panel:
        items: list[Item] = []
        derived = self.i.derived
        for pr in self.mine:
            if pr.is_draft or approved(pr) or pr.requested_users or self._iced(pr):
                continue
            details: list[str] = []
            if pr.requested_teams:
                details.append("requested from team: " + ", ".join(pr.requested_teams))
            else:
                details.append("no reviewers requested")
            if derived and (suggestions := derived.suggestions.get(pr.url)):
                details.append(
                    "recent committers here: "
                    + ", ".join(f"{s.login} ({s.commits})" for s in suggestions)
                )
            items.append(_item(self.now, pr, details=details, rank=-pr.created_at.timestamp()))
        return Panel(
            key="needs_reviewer",
            title="Needs a reviewer",
            description="My open PRs with no person requested and no approval yet.",
            items=items,
        )

    def feedback(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            if self._iced(pr):
                continue
            threads = unresolved_threads(pr)
            details = [
                f"{'(outdated) ' if t.outdated else ''}{t.author or '?'} on {t.path or 'PR'}:"
                f" {t.body.strip().splitlines()[0][:120] if t.body.strip() else ''}"
                for t in threads
            ]
            links = [Link(text=f"thread {n}", url=t.url) for n, t in enumerate(threads, 1) if t.url]
            if changes_requested(pr):
                details.insert(0, "changes requested")
            details += unanswered_feedback(pr, self.me)
            if details:
                items.append(_item(self.now, pr, details=details, links=links, rank=len(details)))
        return Panel(
            key="feedback",
            title="Feedback to address",
            description="Unresolved review threads, changes requested, and comments since my last push.",
            items=items,
        )

    def ci_failures(self) -> Panel:
        items: list[Item] = []
        cutoff = self.now - MERGE_QUEUE_LOOKBACK
        for pr in self.mine:
            if pr.is_draft or self._iced(pr):
                continue
            failing = [c for c in failing_checks(pr) if c.name not in self.i.review_checks]
            details = [f"{c.name}: {c.state}" for c in failing]
            links = [Link(text=c.name, url=c.url) for c in failing if c.url]
            for event in pr.events:
                if event.type == "RemovedFromMergeQueueEvent" and event.at >= cutoff:
                    details.append(
                        f"removed from merge queue {age(self.now, event.at)} ago"
                        + (f": {event.reason}" if event.reason else "")
                    )
            if details:
                items.append(_item(self.now, pr, details=details, links=links, rank=len(failing)))
        return Panel(
            key="ci_failures",
            title="CI failures",
            description="Failing checks and recent merge-queue evictions on my non-draft PRs.",
            items=items,
        )

    def conflicts(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            if has_conflicts(pr) and not self._iced(pr):
                items.append(
                    _item(
                        self.now,
                        pr,
                        details=[f"mergeable: {pr.mergeable}, state: {pr.merge_state}"],
                    )
                )
        return Panel(
            key="conflicts",
            title="Merge conflicts",
            description="My open PRs that conflict with their base.",
            items=items,
        )

    def ready_to_merge(self) -> tuple[Panel, Panel]:
        ready: list[Item] = []
        held: list[Item] = []
        for pr in self.mine:
            if pr.is_draft or self._iced(pr) or pr.in_merge_queue:
                continue
            reasons: list[str] = []
            if not approved(pr):
                reasons.append("not approved")
            if unresolved_threads(pr):
                reasons.append("unresolved threads")
            if failing_checks(pr):
                reasons.append("failing checks")
            if pending_checks(pr):
                reasons.append("checks pending")
            if has_conflicts(pr):
                reasons.append("conflicts")
            if pr.default_branch and pr.base != pr.default_branch:
                reasons.append(f"stacked on {pr.base}")
            if reasons:
                continue
            conditions = self._held_by(pr)
            if conditions:
                held.append(_item(self.now, pr, details=[f"held: {c}" for c in conditions]))
            else:
                ready.append(_item(self.now, pr, details=["approved, green, base is default"]))
        return (
            Panel(
                key="ready_to_merge",
                title="Ready to merge",
                description="Approved, green, no open threads, based on the default branch.",
                items=ready,
            ),
            Panel(
                key="held",
                title="Held by merge conditions",
                description="Would be ready, but the work notes hold them.",
                items=held,
            ),
        )

    def metadata(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            if self._iced(pr):
                continue
            problems = [label for label in pr.labels if label in self.i.placeholder_labels]
            keys = ticket_keys(pr)
            prefix = self.i.ticket_link_prefix
            if keys and prefix and not _has_ticket_link_line(pr.body, prefix):
                problems.append(f"no {prefix} line for {keys[0]}")
            if not pr.body.strip():
                problems.append("empty description")
            if pr.is_draft and pr.rollup_state == "SUCCESS" and not unresolved_threads(pr):
                todos = [t for item in self._work_items_for(pr) for t in item.todos]
                if not todos:
                    problems.append("draft with green CI and no open todos: ready?")
            if problems:
                items.append(_item(self.now, pr, details=problems))
        return Panel(
            key="metadata",
            title="PR housekeeping",
            description="Unknown labels, missing ticket links, drafts that look ready.",
            items=items,
        )

    # Team

    def team_prs(self) -> Panel:
        gh = self.i.github
        items: list[Item] = []
        if gh:
            direct = {pr.url for pr in gh.direct_requests}
            scores = self.i.derived.team_scores if self.i.derived else {}
            for pr in gh.team_requests:
                if pr.url in direct or pr.is_draft:
                    continue
                score = scores.get(pr.url)
                details: list[str] = []
                rank = 0.0
                if score:
                    details.append(
                        f"{score.owned_files} files owned by my teams"
                        f" ({score.required_files} required), {score.my_files} in dirs I work in"
                    )
                    rank = score.required_files * 100 + score.owned_files + score.my_files * 10
                if approved(pr):
                    details.append("already approved")
                    rank -= 1000
                items.append(_item(self.now, pr, details=details, rank=rank))
        return Panel(
            key="team_prs",
            title="Requested from my teams",
            description=f"PRs waiting on {', '.join(self.i.my_teams) or 'my teams'}, ranked by"
            " CODEOWNERS and the paths I work in.",
            items=items,
        )

    # Local

    def local_work(self) -> Panel:
        items: list[Item] = []
        local = self.i.local
        if not local:
            return Panel(key="local_work", title="Local work", description="", items=[])
        for repo in local.repos:
            for wt in repo.worktrees:
                if wt.modified or wt.untracked:
                    pr = self.open_by_branch.get((repo.github_repo or "", wt.branch or ""))
                    actions: list[Action] = []
                    if wt.modified:
                        actions.append(
                            Action(
                                name="wip_commit",
                                label="WIP commit",
                                fields={"repo": repo.dir, "worktree": wt.path},
                            )
                        )
                    if wt.branch and wt.branch not in self.iced_branches:
                        actions.append(_ice_action(repo.dir, wt.branch))
                    items.append(
                        Item(
                            title=f"{wt.branch or 'detached HEAD'}: {wt.modified} modified,"
                            f" {wt.untracked} untracked",
                            url=pr.url if pr else None,
                            subtitle=wt.path,
                            badges=["uncommitted"],
                            rank=2,
                            actions=actions,
                        )
                    )
            for branch in repo.branches:
                if branch.name == repo.default_branch or not self._is_my_branch(
                    repo.github_repo, branch.name
                ):
                    continue
                reason = None
                if branch.ahead:
                    reason = f"{branch.ahead} unpushed commits"
                elif branch.upstream is None and branch.ahead_of_default:
                    reason = f"{branch.ahead_of_default} commits, never pushed"
                if not reason:
                    continue
                pr = self.open_by_branch.get((repo.github_repo or "", branch.name))
                iced = branch.name in self.iced_branches
                actions = []
                if not branch.behind:
                    actions.append(
                        Action(
                            name="push_branch",
                            label="Push",
                            fields={"repo": repo.dir, "branch": branch.name},
                        )
                    )
                if not iced:
                    actions.append(_ice_action(repo.dir, branch.name))
                items.append(
                    Item(
                        title=f"{branch.name}: {reason}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · last commit {age(self.now, branch.committed_at)} ago",
                        badges=(["iced"] if iced else []) + (["has PR"] if pr else ["no PR"]),
                        rank=0 if iced else 1,
                        actions=actions,
                    )
                )
        return Panel(
            key="local_work",
            title="Local work",
            description="Uncommitted changes in worktrees, and branches with unpushed commits.",
            items=items,
        )

    def uses_git_stack(self) -> bool:
        """Whether any repo has tracked stacks; without them the Stacks panel is left out."""
        local = self.i.local
        return bool(local) and any(repo.stack for repo in local.repos)

    def stacks(self) -> Panel:
        items: list[Item] = []
        actions: list[Action] = []
        local = self.i.local
        iced_repo_dirs = {_expand(stack.repo_dir) for stack in self.i.iced_stacks}
        for repo in local.repos if local else []:
            merged = [b.name for b in repo.stack if b.status == "merged"]
            if merged and repo.dir in iced_repo_dirs:
                items.append(
                    Item(
                        title=f"{len(merged)} merged stack branches not cleaned",
                        subtitle=repo.dir,
                        details=["this repo has iced stacks, so `git stack clean` is left to you"],
                        rank=-1,
                    )
                )
            elif merged:
                actions.append(
                    Action(
                        name="stack_clean",
                        label=f"Clean {len(merged)} merged in {Path(repo.dir).name}",
                        fields={"repo": repo.dir},
                        confirm=f"Run `git stack clean -d local` in {repo.dir}? It fetches,"
                        " fast-forwards main, untracks merged branches and deletes them locally.",
                    )
                )
            for b in repo.stack:
                if b.status in (None, "up-to-date"):
                    continue
                pr = self.open_by_branch.get((repo.github_repo or "", b.name))
                badges = ["iced"] if b.name in self.iced_branches else []
                items.append(
                    Item(
                        title=f"{b.name}: {b.status}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · on {b.parent}",
                        badges=badges,
                        rank=0 if badges else 1,
                    )
                )
            for b in repo.stack:
                if b.parent is None or b.status == "merged" or b.name in self.iced_branches:
                    continue
                key = (repo.github_repo or "", b.name)
                if key not in self.open_by_branch and key not in self.merged_by_branch:
                    items.append(
                        Item(
                            title=f"{b.name}: tracked, no PR",
                            subtitle=f"{repo.dir} · on {b.parent}",
                            rank=0.5,
                        )
                    )
        return Panel(
            key="stacks",
            title="Stacks",
            description="git-stack branches that need a restack, were merged, or have no PR.",
            items=items,
            actions=actions,
        )

    def cleanup(self) -> Panel:
        items: list[Item] = []
        targets: list[str] = []
        local = self.i.local
        for repo in local.repos if local else []:
            for b in repo.branches:
                merged = self.merged_by_branch.get((repo.github_repo or "", b.name))
                if merged:
                    title = f"{b.name}: PR merged {age(self.now, merged.merged_at)} ago"
                    subtitle = repo.dir
                elif b.gone and not b.ahead and self._is_my_branch(repo.github_repo, b.name):
                    title = f"{b.name}: upstream gone"
                    subtitle = f"{repo.dir} · last commit {age(self.now, b.committed_at)} ago"
                else:
                    continue
                targets.append(f"{repo.dir}{TARGET_SEPARATOR}{b.name}")
                items.append(
                    Item(
                        title=title,
                        url=merged.url if merged else None,
                        subtitle=subtitle,
                        badges=["git branch -D"],
                        actions=[
                            Action(
                                name="delete_branch",
                                label="Delete",
                                fields={"repo": repo.dir, "branch": b.name},
                                confirm=f"Delete local branch {b.name}?",
                            )
                        ],
                    )
                )
        actions = (
            [
                Action(
                    name="delete_branches",
                    label=f"Delete all {len(targets)}",
                    fields={"targets": "\n".join(targets)},
                    confirm=f"Delete all {len(targets)} local branches listed here?",
                )
            ]
            if targets
            else []
        )
        return Panel(
            key="cleanup",
            title="Cleanup",
            description="Local branches whose PR merged or whose remote branch is gone.",
            items=items,
            actions=actions,
        )

    # Nudges

    def _ping_targets(self, pr: PullRequest, last_by_target: dict[str, Ping]) -> list[PingTarget]:
        targets: list[PingTarget] = []
        workspace = self.i.slack_workspace_url
        for login in pr.requested_users:
            user = self.i.users.get(login)
            link = (
                _slack_link(workspace, f"team/{user.slack_id}") if user and user.slack_id else None
            )
            targets.append(
                self._target(
                    "user", login, link, f"{login} (DM)", last_by_target.get(f"user:{login}"), pr
                )
            )
        for slug in pr.requested_teams:
            team = self.i.teams.get(slug)
            link = None
            destination = f"team {slug}"
            if team and team.channel_id:
                link = _slack_link(workspace, f"archives/{team.channel_id}")
                destination = f"{team.channel_name or slug} ({team.channel_id})"
            elif team and team.channel_name:
                destination = team.channel_name
            targets.append(
                self._target(
                    "team", slug, link, destination, last_by_target.get(f"team:{slug}"), pr
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
        pr: PullRequest,
    ) -> PingTarget:
        ping_kind: Literal["first", "follow-up"] = "follow-up" if last else "first"
        return PingTarget(
            kind=kind,
            name=name,
            slack_link=link,
            destination=destination,
            ping_kind=ping_kind,
            last_ping=last.sent_at if last else None,
            message=self.i.ping_first.format(url=pr.url)
            if ping_kind == "first"
            else self.i.ping_follow_up,
        )

    def pings(self) -> dict[str, PrPing]:
        by_pr: dict[str, dict[str, Ping]] = defaultdict(dict)
        any_ping: dict[str, Ping] = {}
        for ping in self.i.pings:
            by_pr[ping.pr_url][ping.target] = ping
            any_ping[ping.pr_url] = ping
        result: dict[str, PrPing] = {}
        stale_after = timedelta(hours=self.i.stale_review_hours)
        for pr in self.mine:
            if pr.is_draft or self._iced(pr):
                continue
            blockers: list[str] = []
            if approved(pr):
                blockers.append("already approved")
            if not pr.requested_users and not pr.requested_teams:
                blockers.append("no reviewers requested")
            if has_conflicts(pr):
                blockers.append("conflicts")
            if any(c.name not in self.i.review_checks for c in failing_checks(pr)):
                blockers.append("failing checks")
            if unresolved_threads(pr):
                blockers.append("unresolved threads")
            if unanswered_feedback(pr, self.me):
                blockers.append("unanswered feedback")
            last = last_review_activity(pr)
            last_by_target = dict(by_pr.get(pr.url, {}))
            # A nudge the skill logged without a target counts for every target.
            if (untargeted := any_ping.get(pr.url)) and untargeted.target.startswith("unknown"):
                for login in pr.requested_users:
                    last_by_target.setdefault(f"user:{login}", untargeted)
                for slug in pr.requested_teams:
                    last_by_target.setdefault(f"team:{slug}", untargeted)
            result[pr.url] = PrPing(
                pr_url=pr.url,
                targets=self._ping_targets(pr, last_by_target),
                blockers=blockers,
                stale=last is None or self.now - last >= stale_after,
            )
        return result

    def nudges(self, pings: dict[str, PrPing]) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            ping = pings.get(pr.url)
            if not ping or ping.blockers or not ping.stale:
                continue
            details = [
                f"{t.ping_kind} ping to {t.destination}"
                + (f" (last {age(self.now, t.last_ping)} ago)" if t.last_ping else "")
                for t in ping.targets
            ]
            last = last_review_activity(pr)
            details.append(f"last review activity {age(self.now, last)} ago")
            items.append(
                _item(self.now, pr, details=details, rank=-(last or pr.created_at).timestamp())
            )
        return Panel(
            key="nudges",
            title="Nudge reviewers",
            description="In order (green, no conflicts, no open threads) and quiet for a day.",
            items=items,
        )

    # Jira and notes

    def jira(self) -> tuple[Panel, Panel]:
        transitions: list[Item] = []
        ranked: list[Item] = []
        jira = self.i.jira
        if not jira:
            return (
                Panel(key="jira_transitions", title="Jira transitions", description="", items=[]),
                Panel(key="jira_next", title="What next", description="", items=[]),
            )
        prs_by_key: dict[str, list[PullRequest]] = defaultdict(list)
        gh = self.i.github
        for pr in self.mine + (gh.merged_recent if gh else []):
            for key in ticket_keys(pr):
                prs_by_key[key].append(pr)
        st = self.i.jira_statuses
        for issue in jira.issues:
            prs = prs_by_key.get(issue.key, [])
            open_prs = [p for p in prs if p.state == "OPEN"]
            merged = [p for p in prs if p.state == "MERGED"]
            proposal = None
            if (
                issue.status == st.in_progress
                and open_prs
                and all(not p.is_draft for p in open_prs)
            ):
                proposal = f"{st.in_progress} → {st.in_review}: all PRs open and non-draft"
            elif issue.status == st.in_review and merged and not open_prs:
                proposal = f"{st.in_review} → {st.closed}: all PRs merged"
            elif issue.status == st.in_review and any(
                p.is_draft or changes_requested(p) for p in open_prs
            ):
                proposal = (
                    f"{st.in_review} → {st.in_progress}: a PR is draft or has changes requested"
                )
            elif issue.status_category == "new" and prs:
                proposal = f"still {issue.status} but has PRs"
            if proposal:
                transitions.append(
                    Item(
                        title=f"{issue.key} {issue.summary}",
                        url=issue.url,
                        subtitle=proposal,
                        links=[Link(text=f"#{p.number}", url=p.url) for p in prs],
                    )
                )
        for issue in jira.issues:
            kind = issue.type.lower()
            escalation = _escalation(
                issue, self.i.escalation_label_prefix, self.i.escalation_levels
            )
            if any(t in kind for t in self.i.incident_types):
                rank = 3000 + escalation * 100
                why = "incident" + (f", escalated ({escalation})" if escalation else "")
            elif any(t in kind for t in self.i.bug_types):
                rank = 2000 + escalation * 100
                why = "bug" + (f", escalated ({escalation})" if escalation else "")
            elif not issue.open_blockers:
                rank = 1000
                why = "unblocked" + (f", under {issue.parent}" if issue.parent else "")
            else:
                rank = 0
                why = "blocked by " + ", ".join(issue.open_blockers)
            rank += min(999, (self.now - issue.created).days)
            ranked.append(
                Item(
                    title=f"{issue.key} {issue.summary}",
                    url=issue.url,
                    subtitle=f"{issue.status} · {issue.priority or ''} · {age(self.now, issue.created)} old",
                    badges=[why],
                    rank=rank,
                )
            )
        return (
            Panel(
                key="jira_transitions",
                title="Jira transitions",
                description="Status changes the PR states suggest.",
                items=transitions,
            ),
            Panel(
                key="jira_next",
                title="What next",
                description="My open tickets: incidents by escalation, then bugs, then unblocked work.",
                items=ranked,
            ),
        )

    def with_add_todo(self, panel: Panel) -> Panel:
        """Give each of my PRs, each Jira ticket and each work-notes entry an Add todo field.

        The todo goes to the entry covering the PR or ticket; without one, an entry is created,
        titled with the ticket key and summary (or the PR's URL and title, as the skill does).
        """
        gh = self.i.github
        mine = {pr.url: pr for pr in gh.mine} if gh else {}
        issues = {i.key: i for i in self.i.jira.issues} if self.i.jira else {}
        entries = {item.title for item in self.i.work_items}
        items: list[Item] = []
        for item in panel.items:
            action: Action | None = None
            if any(a.name == "todo_add" for a in item.actions):
                pass
            elif panel.key == "todos" and item.title in entries:
                action = add_todo_action(item.title)
            elif pr := mine.get(item.url or ""):
                keys = ticket_keys(pr)
                entry = entry_for(self.i.work_items, pr.url, keys)
                issue = issues.get(keys[0]) if keys else None
                title = (
                    f"{keys[0]} {issue.summary if issue else pr.title}"
                    if keys
                    else f"{pr.url} {pr.title}"
                )
                action = add_todo_action(entry, title, pr.url)
            elif item.url and (match := JIRA_KEY_RE.search(item.url.rsplit("/", 1)[-1])):
                key = match.group(0)
                issue = issues.get(key)
                entry = entry_for(self.i.work_items, item.url, [key])
                title = f"{key} {issue.summary}" if issue else key
                action = add_todo_action(entry, title, item.url)
            items.append(
                item.model_copy(update={"actions": [*item.actions, action]}) if action else item
            )
        return panel.model_copy(update={"items": items})

    def _pr_states(self) -> dict[str, PrState]:
        gh = self.i.github
        if gh is None:
            return {}
        states: dict[str, PrState] = {}
        groups: list[tuple[list[PullRequest], PrState]] = [
            (gh.mine, "open"),
            (gh.merged_recent, "merged"),
            (gh.closed_recent, "closed"),
        ]
        for prs, state in groups:
            for pr in prs:
                states[pr.url] = state
        return states

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
                            self.i.jira_site,
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


def build(inputs: Inputs) -> Dashboard:
    b = _Builder(inputs)
    pings = b.pings()
    ready, held = b.ready_to_merge()
    transitions, jira_next = b.jira()
    panels = [
        b.reviews_for_me(),
        b.feedback(),
        b.ci_failures(),
        b.conflicts(),
        b.needs_reviewer(),
        b.nudges(pings),
        ready,
        held,
        b.local_work(),
        *([b.stacks()] if b.uses_git_stack() else []),
        b.team_prs(),
        b.todos(),
        transitions,
        jira_next,
        b.metadata(),
        b.cleanup(),
        b.iced(),
    ]
    return Dashboard(
        generated_at=inputs.now,
        login=inputs.login,
        panels=[_sorted(b.with_add_todo(p)) for p in panels],
        pings=pings,
        sources=inputs.sources,
    )
