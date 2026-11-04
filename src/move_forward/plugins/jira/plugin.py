"""The Jira plugin: my open tickets, the status changes their PRs suggest, and what's next."""

import json
import math
import re
from datetime import date, datetime, time, timedelta
from typing import ClassVar

from pydantic import BaseModel

from ... import localtime
from ...act_now import due_pressure
from ...asking import Asker, InitSetup
from ...domain import Model
from ...graph import Entity, Graph, Ref, attrs
from ...notifications import Event
from ...page import Action, Item, Link, Panel, Signals, age
from ...plugin import ActionHandler, Context, Plugin
from . import actions, changes, client
from .config import JiraConfig
from .kinds import TicketAttrs
from .models import JiraIssue, JiraSnapshot
from .repository import MIGRATIONS, IssueRepository
from .setup import EXAMPLE, TEMPLATE, questions

TICKET = "ticket"
PR = "pr"
CLOSEABLE = "closeable"
"""The kv key of the tickets already said to be closeable."""
CHECKED = "changes_checked_at"
"""The kv key of when `changed` last looked."""


def ticket_ref(key: str) -> Ref:
    return Ref(kind=TICKET, id=key)


def escalation(issue: JiraIssue, label_prefix: str, levels: list[str]) -> int:
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


def priority_level(issue: JiraIssue, priorities: list[str]) -> int:
    """An issue's priority as its 1-based position in `priorities` (lowest first); 0 when it
    has none or one that isn't listed."""
    return priorities.index(issue.priority) + 1 if issue.priority in priorities else 0


def _first(names: list[str]) -> str:
    """The name a proposal shows for a status with several."""
    return names[0] if names else "?"


class _PrView(Model):
    """What jira reads of a `pr` (github's), to suggest status changes."""

    id: str
    url: str | None = None
    state: str | None = None
    number: int
    draft: bool
    mine: bool
    changes_requested: bool
    progress: str | None = None
    """github's word for where my open PR is: `ready to merge`, `CI failing`, ..."""


def in_status(issue: JiraIssue, names: list[str]) -> bool:
    """Whether the issue's status is any of `names`, without regard to case."""
    return issue.status.casefold() in {name.casefold() for name in names}


def _advance_rollout(issue: JiraIssue, prs: list[_PrView]) -> Item:
    """The item for a ticket in rollout with PRs still open, listing where each is. Low
    urgency, unless one is ready to merge: merging the next one is the step. Act now joins it
    to the best item about one of its PRs ("Merge, then: Advance the rollout")."""
    lines = [f"{p.progress or p.state}: #{p.number}" for p in prs]
    ready = any(p.progress == "ready to merge" for p in prs)
    lines.sort(key=lambda line: not line.startswith("ready to merge"))
    return Item(
        title=f"{issue.key} {issue.summary}",
        url=issue.url,
        subtitle=f"{issue.status}: PRs still open",
        details=lines,
        links=[Link(text=f"#{p.number}", url=p.url or p.id) for p in prs],
        subjects=[ticket_ref(issue.key)],
        signals=Signals(
            verb="Advance the rollout",
            importance=0 if ready else -1,
            effort="minutes" if ready else "hour",
        ),
    )


def _finish_rollout(config: JiraConfig, issue: JiraIssue, prs: list[_PrView]) -> Item:
    """The low-urgency item for a ticket in rollout whose PRs have all merged: close it once
    monitoring is done."""
    closed = config.status_closed
    return Item(
        title=f"{issue.key} {issue.summary}",
        url=issue.url,
        subtitle=f"{issue.status}: all PRs merged",
        details=["all PRs merged; close once monitoring is done"],
        links=[Link(text=f"#{p.number}", url=p.url or p.id) for p in prs],
        subjects=[ticket_ref(issue.key)],
        actions=[
            Action(
                name="jira/transition",
                label="Close",
                fields={"key": issue.key, "to": closed[0]}
                | ({"also": "\n".join(closed[1:])} if closed[1:] else {}),
            )
        ]
        if closed
        else [],
        signals=Signals(verb="Finish the rollout", importance=-1, effort="hour"),
    )


def transitions(config: JiraConfig, snapshot: JiraSnapshot, graph: Graph) -> Panel:
    items: list[Item] = []
    for issue in snapshot.issues:
        prs = [p for p in graph.view_related(ticket_ref(issue.key), PR, _PrView) if p.mine]
        open_prs = [p for p in prs if p.state == "open"]
        merged = [p for p in prs if p.state == "merged"]
        closed = [p for p in prs if p.state == "closed"]
        proposal = None
        targets: list[str] = []
        closing = False
        if in_status(issue, config.status_rollout):
            if open_prs:
                items.append(_advance_rollout(issue, prs))
            elif merged:
                items.append(_finish_rollout(config, issue, prs))
            continue
        if (
            in_status(issue, config.status_in_progress)
            and open_prs
            and all(not p.draft for p in open_prs)
        ):
            targets = config.status_in_review
            proposal = f"{issue.status} → {_first(targets)}: all PRs open and non-draft"
        elif in_status(issue, config.status_in_review) and merged and not open_prs:
            targets = config.status_closed
            closing = True
            proposal = f"{issue.status} → {_first(targets)}: all PRs merged"
        elif in_status(issue, config.status_in_review) and any(
            p.draft or p.changes_requested for p in open_prs
        ):
            targets = config.status_in_progress
            proposal = f"{issue.status} → {_first(targets)}: a PR is draft or has changes requested"
        elif (
            (
                in_status(issue, config.status_in_progress)
                or in_status(issue, config.status_in_review)
            )
            and closed
            and not open_prs
            and not merged
        ):
            numbers = ", ".join(f"#{p.number}" for p in closed)
            proposal = f"PR {numbers} closed unmerged: reopen it or move the ticket back"
            if in_status(issue, config.status_in_review):
                targets = config.status_in_progress
        elif issue.status_category == "new" and prs:
            proposal = f"still {issue.status} but has PRs"
        to = targets[0] if targets else None
        if proposal:
            items.append(
                Item(
                    title=f"{issue.key} {issue.summary}",
                    url=issue.url,
                    subtitle=proposal,
                    links=[Link(text=f"#{p.number}", url=p.url or p.id) for p in prs],
                    subjects=[ticket_ref(issue.key)],
                    actions=[
                        Action(
                            name="jira/transition",
                            label=f"Move to {to}",
                            fields={"key": issue.key, "to": to}
                            | ({"also": "\n".join(targets[1:])} if targets[1:] else {}),
                        )
                    ]
                    if to
                    else [],
                    signals=Signals(
                        verb=f"Move to {to}" if to else "Update the status",
                        effort="minutes",
                        closes=issue.key if closing else None,
                    ),
                )
            )
    return Panel(
        key="jira_transitions",
        priority=120,  # 12. Jira updates
        act_now=True,
        title="Jira transitions",
        description="Status changes the PR states suggest.",
        items=items,
    )


def end_of(day: date, now: datetime) -> datetime:
    """When a due date runs out: the end of that day where I am."""
    return datetime.combine(day + timedelta(days=1), time(), localtime.local(now).tzinfo)


def what_next(config: JiraConfig, snapshot: JiraSnapshot, now: datetime) -> Panel:
    """My open tickets in the order to take them, each with signals for Act now: an incident
    is important, critical once escalated, and an escalated bug important (one step more
    at the top priority); a due date
    counts, and a ticket with open blockers, or in the blocked status, is blocked."""
    items: list[Item] = []
    for issue in snapshot.issues:
        kind = issue.type.lower()
        level = escalation(issue, config.escalation_label_prefix, config.escalation_levels)
        priority = priority_level(issue, config.priorities)
        top = bool(config.priorities) and priority == len(config.priorities)
        blocked = bool(issue.open_blockers) or in_status(issue, config.status_blocked)
        if any(t in kind for t in config.incident_types):
            rank = 3000 + level * 100 + priority * 20
            why = "incident" + (f", escalated ({level})" if level else "")
            why += ", blocked" if blocked else ""
            importance, verb = min(2, (2 if level else 1) + top), "Look at the incident"
        elif any(t in kind for t in config.bug_types):
            rank = 2000 + level * 100 + priority * 20
            why = "bug" + (f", escalated ({level})" if level else "")
            why += ", blocked" if blocked else ""
            importance, verb = min(2, (1 if level else 0) + top), "Fix the bug"
        elif in_status(issue, config.status_rollout):
            rank = -1000  # below blocked work: nothing to do but watch
            why = "in rollout"
            importance, verb = 0, "Watch the rollout"
        elif not blocked:
            rank = 1000
            why = "unblocked" + (f", under {issue.parent}" if issue.parent else "")
            importance, verb = 0, "Work on it"
        else:
            rank = 0
            why = (
                "blocked by " + ", ".join(issue.open_blockers) if issue.open_blockers else "blocked"
            )
            importance, verb = 0, "Chase the blockers"
        rank += min(999, (now - issue.created).days)
        badges = [why] + ([f"due {issue.due:%b} {issue.due.day}"] if issue.due else [])
        items.append(
            Item(
                title=f"{issue.key} {issue.summary}",
                url=issue.url,
                subtitle=f"{issue.status} · {issue.priority or ''} · {age(now, issue.created)} old",
                badges=badges,
                rank=rank,
                subjects=[ticket_ref(issue.key)],
                signals=None  # the rollout's own item asks for what's left
                if in_status(issue, config.status_rollout)
                and not any(t in kind for t in (*config.incident_types, *config.bug_types))
                else Signals(
                    verb=verb,
                    importance=importance,
                    due=end_of(issue.due, now) if issue.due else None,
                    blocked=blocked,
                    touched=issue.updated,
                ),
            )
        )
    return Panel(
        key="jira_next",
        priority=130,  # 13. what next
        title="What next",
        description="My open tickets: incidents by escalation, then bugs, then unblocked work.",
        items=items,
    )


def key_pattern(projects: list[str]) -> str | None:
    """A pattern for the keys of tickets in `projects`, or None when there are none."""
    if not projects:
        return None
    alternatives = "|".join(re.escape(p) for p in sorted(projects, key=len, reverse=True))
    return rf"\b(?:{alternatives})-\d+\b"


class JiraPlugin(Plugin[JiraConfig, JiraSnapshot]):
    name = "jira"
    title = "Jira"
    init_template = TEMPLATE
    init_example = EXAMPLE

    def init_suggested(self) -> bool:
        """Only when asked for: it needs a site (jira) or workspace (slack) to be useful."""
        return False

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    config_model = JiraConfig
    snapshot_model = JiraSnapshot
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {TICKET: TicketAttrs}
    linking: ClassVar[dict[str, str | None]] = {TICKET: None}
    """Tickets link; `linking_patterns` gives the pattern of their keys."""

    notification_kinds: ClassVar[dict[str, str]] = changes.KINDS

    def events(
        self, ctx: Context[JiraConfig], before: JiraSnapshot, after: JiraSnapshot
    ) -> list[Event]:
        return changes.changes(ctx.config, before, after)

    def graph_events(self, ctx: Context[JiraConfig], graph: Graph) -> list[Event]:
        """`closeable`: a ticket the transitions panel now proposes closing (its PRs are
        merged), once. The first time only keeps what's there."""
        snapshot = self.load(ctx) or JiraSnapshot(issues=[])
        issues = {i.key: i for i in snapshot.issues}
        closing = {
            item.actions[0].fields["key"]
            for item in transitions(
                ctx.config, JiraSnapshot(issues=list(issues.values())), graph
            ).items
            if item.actions and item.signals and item.signals.closes
        }
        said = ctx.kv.get(CLOSEABLE)
        ctx.kv.set(CLOSEABLE, json.dumps(sorted(closing)))
        if said is None:
            return []
        known = set(json.loads(said))
        return [changes.closeable(issues[key]) for key in sorted(closing - known) if key in issues]

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS

    def collect(self, ctx: Context[JiraConfig]) -> JiraSnapshot:
        return client.collect(ctx.config, ctx.email)

    def store(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot) -> None:
        IssueRepository(ctx.db).replace(snapshot)

    change_check_seconds: ClassVar[int | None] = 60

    def changed(self, ctx: Context[JiraConfig], graph: Graph) -> bool:
        """Whether a ticket of mine was updated since the last look (and a minute before
        it, as Jira counts in whole minutes): a one-result search."""
        checked = ctx.kv.get(CHECKED)
        found = False
        if checked is not None:
            seconds = (ctx.now - datetime.fromisoformat(checked)).total_seconds()
            minutes = math.ceil(seconds / 60) + 1
            found = client.updated_within(ctx.config, ctx.email, minutes)
        ctx.kv.set(CHECKED, ctx.now.isoformat())
        return found

    def load(self, ctx: Context[JiraConfig]) -> JiraSnapshot | None:
        return IssueRepository(ctx.db).load()

    def linking_patterns(self, ctx: Context[JiraConfig]) -> dict[str, str | None]:
        """Keys of the projects my tickets have been in, so `UTF-8` or `SHA-256` in a PR
        isn't taken for a ticket; none at all before jira has seen a ticket of mine."""
        return {TICKET: key_pattern(IssueRepository(ctx.db).projects())}

    def entities(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None) -> list[Entity]:
        return [
            Entity(
                ref=ticket_ref(issue.key),
                title=f"{issue.key} {issue.summary}",
                url=issue.url,
                state=issue.status,
                attrs=attrs(
                    TicketAttrs(
                        summary=issue.summary,
                        status=issue.status,
                        status_category=issue.status_category,
                        type=issue.type,
                    )
                ),
            )
            for issue in (snapshot.issues if snapshot else [])
        ]

    def fetch(self, ctx: Context[JiraConfig], kind: str, ids: list[str]) -> list[Entity]:
        """Tickets by key, open or resolved, with `resolved_at` (null while open). Their
        projects are remembered, so their keys link from then on (see `linking_patterns`)."""
        keys = [k for k in ids if client.is_key(k)]
        if kind != TICKET or not keys:
            return []
        token = client.token(ctx.config, ctx.email)
        found = client.tickets(ctx.config, ctx.email, keys, token)
        IssueRepository(ctx.db).remember_projects([i.key for i in found])
        return [
            Entity(
                ref=ticket_ref(i.key),
                title=f"{i.key} {i.summary}",
                url=i.url,
                state=i.status,
                attrs={"resolved_at": i.resolved.isoformat() if i.resolved else None},
            )
            for i in found
        ]

    def url_for(self, ctx: Context[JiraConfig], kind: str, id: str) -> str | None:
        return f"{ctx.config.site.rstrip('/')}/browse/{id}" if kind == TICKET else None

    def panels(
        self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None, graph: Graph
    ) -> list[Panel]:
        if snapshot is None:
            return []
        return [transitions(ctx.config, snapshot, graph), what_next(ctx.config, snapshot, ctx.now)]

    def act_now(
        self,
        ctx: Context[JiraConfig],
        snapshot: JiraSnapshot | None,
        graph: Graph,
        panels: list[Panel],
    ) -> list[Item]:
        """Its transitions, and the tickets in What next that matter now: important ones
        (incidents, escalated bugs), those due within the week, and blocked ones, which Act
        now ranks down until they've sat untouched for a while."""
        nominated = super().act_now(ctx, snapshot, graph, panels)
        for panel in panels:
            if panel.key != "jira_next":
                continue
            for item in panel.items:
                signals = item.signals or Signals()
                due_soon = signals.due is not None and due_pressure(signals.due, ctx.now) > 0
                if signals.importance or due_soon or signals.blocked:
                    nominated.append(item)
        return nominated
