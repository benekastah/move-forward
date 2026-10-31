"""The Jira plugin: my open tickets, the status changes their PRs suggest, and what's next."""

from datetime import datetime

from ...domain import JiraIssue, JiraSnapshot
from ...graph import Entity, Graph, Ref
from ...page import Item, Link, Panel, age
from ...plugin import Context, Plugin
from . import client
from .config import JiraConfig

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


def transitions(config: JiraConfig, snapshot: JiraSnapshot, graph: Graph) -> Panel:
    items: list[Item] = []
    for issue in snapshot.issues:
        prs = [p for p in graph.related(ticket_ref(issue.key), PR) if p.attrs.get("mine")]
        open_prs = [p for p in prs if p.state == "open"]
        merged = [p for p in prs if p.state == "merged"]
        proposal = None
        if (
            issue.status == config.status_in_progress
            and open_prs
            and all(not p.attrs.get("draft") for p in open_prs)
        ):
            proposal = f"{config.status_in_progress} → {config.status_in_review}: all PRs open and non-draft"
        elif issue.status == config.status_in_review and merged and not open_prs:
            proposal = f"{config.status_in_review} → {config.status_closed}: all PRs merged"
        elif issue.status == config.status_in_review and any(
            p.attrs.get("draft") or p.attrs.get("changes_requested") for p in open_prs
        ):
            proposal = (
                f"{config.status_in_review} → {config.status_in_progress}:"
                " a PR is draft or has changes requested"
            )
        elif issue.status_category == "new" and prs:
            proposal = f"still {issue.status} but has PRs"
        if proposal:
            items.append(
                Item(
                    title=f"{issue.key} {issue.summary}",
                    url=issue.url,
                    subtitle=proposal,
                    links=[
                        Link(text=f"#{p.attrs.get('number')}", url=p.url or p.ref.id) for p in prs
                    ],
                    subjects=[ticket_ref(issue.key)],
                )
            )
    return Panel(
        key="jira_transitions",
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
        title="What next",
        description="My open tickets: incidents by escalation, then bugs, then unblocked work.",
        items=items,
    )


class JiraPlugin(Plugin[JiraConfig, JiraSnapshot]):
    name = "jira"
    title = "Jira"
    config_model = JiraConfig
    snapshot_model = JiraSnapshot

    def collect(self, ctx: Context[JiraConfig]) -> JiraSnapshot:
        return client.collect(ctx.config, ctx.email)

    def entities(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None) -> list[Entity]:
        return [
            Entity(
                ref=ticket_ref(issue.key),
                title=f"{issue.key} {issue.summary}",
                url=issue.url,
                state=issue.status,
                attrs={
                    "summary": issue.summary,
                    "status": issue.status,
                    "status_category": issue.status_category,
                    "type": issue.type,
                },
            )
            for issue in (snapshot.issues if snapshot else [])
        ]

    def url_for(self, ctx: Context[JiraConfig], kind: str, id: str) -> str | None:
        return f"{ctx.config.site.rstrip('/')}/browse/{id}" if kind == TICKET else None

    def panels(
        self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None, graph: Graph
    ) -> list[Panel]:
        if snapshot is None:
            return []
        return [transitions(ctx.config, snapshot, graph), what_next(ctx.config, snapshot, ctx.now)]
