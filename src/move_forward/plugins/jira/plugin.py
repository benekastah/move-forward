"""The Jira plugin: my open tickets, the status changes their PRs suggest, and what's next."""

import re
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel

from ...domain import Model
from ...graph import Entity, Graph, Ref, attrs
from ...page import Item, Link, Panel, Signals, age
from ...plugin import Context, Plugin
from . import client
from .config import JiraConfig
from .kinds import TicketAttrs
from .models import JiraIssue, JiraSnapshot
from .repository import MIGRATIONS, IssueRepository

TICKET = "ticket"
PR = "pr"


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


class _PrView(Model):
    """What jira reads of a `pr` (github's), to suggest status changes."""

    id: str
    url: str | None = None
    state: str | None = None
    number: int
    draft: bool
    mine: bool
    changes_requested: bool


def transitions(config: JiraConfig, snapshot: JiraSnapshot, graph: Graph) -> Panel:
    items: list[Item] = []
    for issue in snapshot.issues:
        prs = [p for p in graph.view_related(ticket_ref(issue.key), PR, _PrView) if p.mine]
        open_prs = [p for p in prs if p.state == "open"]
        merged = [p for p in prs if p.state == "merged"]
        proposal, to = None, None
        if (
            issue.status == config.status_in_progress
            and open_prs
            and all(not p.draft for p in open_prs)
        ):
            to = config.status_in_review
            proposal = f"{config.status_in_progress} → {to}: all PRs open and non-draft"
        elif issue.status == config.status_in_review and merged and not open_prs:
            to = config.status_closed
            proposal = f"{config.status_in_review} → {to}: all PRs merged"
        elif issue.status == config.status_in_review and any(
            p.draft or p.changes_requested for p in open_prs
        ):
            to = config.status_in_progress
            proposal = f"{config.status_in_review} → {to}: a PR is draft or has changes requested"
        elif issue.status_category == "new" and prs:
            proposal = f"still {issue.status} but has PRs"
        if proposal:
            items.append(
                Item(
                    title=f"{issue.key} {issue.summary}",
                    url=issue.url,
                    subtitle=proposal,
                    links=[Link(text=f"#{p.number}", url=p.url or p.id) for p in prs],
                    subjects=[ticket_ref(issue.key)],
                    signals=Signals(
                        verb=f"Move to {to}" if to else "Update the status",
                        effort="minutes",
                        closes=issue.key if to == config.status_closed else None,
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


def what_next(config: JiraConfig, snapshot: JiraSnapshot, now: datetime) -> Panel:
    items: list[Item] = []
    for issue in snapshot.issues:
        kind = issue.type.lower()
        level = escalation(issue, config.escalation_label_prefix, config.escalation_levels)
        if any(t in kind for t in config.incident_types):
            rank = 3000 + level * 100
            why = "incident" + (f", escalated ({level})" if level else "")
        elif any(t in kind for t in config.bug_types):
            rank = 2000 + level * 100
            why = "bug" + (f", escalated ({level})" if level else "")
        elif not issue.open_blockers:
            rank = 1000
            why = "unblocked" + (f", under {issue.parent}" if issue.parent else "")
        else:
            rank = 0
            why = "blocked by " + ", ".join(issue.open_blockers)
        rank += min(999, (now - issue.created).days)
        items.append(
            Item(
                title=f"{issue.key} {issue.summary}",
                url=issue.url,
                subtitle=f"{issue.status} · {issue.priority or ''} · {age(now, issue.created)} old",
                badges=[why],
                rank=rank,
                subjects=[ticket_ref(issue.key)],
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
    config_model = JiraConfig
    snapshot_model = JiraSnapshot
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {TICKET: TicketAttrs}
    linking: ClassVar[dict[str, str | None]] = {TICKET: None}
    """Tickets link; `linking_patterns` gives the pattern of their keys."""

    def collect(self, ctx: Context[JiraConfig]) -> JiraSnapshot:
        return client.collect(ctx.config, ctx.email)

    def store(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot) -> None:
        IssueRepository(ctx.db).replace(snapshot)

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
