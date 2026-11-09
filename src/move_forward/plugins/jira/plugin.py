"""The Jira plugin: my open tickets, the status changes their PRs suggest, and what's next."""

import math
import re
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar

from ...api import (
    API_VERSION,
    Action,
    ActionHandler,
    ActNowRequest,
    Asker,
    Badge,
    Band,
    Changed,
    ChangedRequest,
    ChangeRule,
    CollectRequest,
    EntitiesRequest,
    Entity,
    Event,
    EventsRequest,
    FetchRequest,
    Graph,
    HandleRequest,
    InitSetup,
    Item,
    KindSpec,
    Link,
    LinkingRequest,
    LoadRequest,
    Migration,
    Model,
    Notify,
    Panel,
    PanelsRequest,
    Plugin,
    Ref,
    Signals,
    StoreRequest,
    UrlRequest,
    age,
    attrs,
    due_pressure,
    localtime,
)
from ...api.kinds import PULL_REQUEST as PR
from ...api.kinds import TICKET
from . import actions, changes, client
from .config import JiraConfig
from .kinds import JiraTicket
from .models import JiraIssue, JiraSnapshot
from .repository import MIGRATIONS, IssueRepository
from .setup import EXAMPLE, TEMPLATE, questions

CHECKED = "changes_checked_at"
"""The kv key of when `changed` last looked."""


def ticket_ref(issue: JiraIssue) -> Ref:
    """A ticket's ref: by its URL, as the ticket family's ids are."""
    return Ref(kind=TICKET, id=issue.url)


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
    order = [p.casefold() for p in priorities]  # as `priority_raised` reads them
    name = (issue.priority or "").casefold()
    return order.index(name) + 1 if name in order else 0


def _first(names: list[str]) -> str:
    """The name a proposal shows for a status with several."""
    return names[0] if names else "?"


class _PrView(Model):
    """What jira reads of a `pull_request`, to suggest status changes."""

    id: str
    url: str | None = None
    state: str | None = None
    handle: str
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
    ready = any(p.progress == "ready to merge" for p in prs)
    ordered = sorted(prs, key=lambda p: p.progress != "ready to merge")  # the step first
    return Item(
        title=f"{issue.key} {issue.summary}",
        url=issue.url,
        subtitle=f"{issue.status}: PRs still open",
        links=[_state_link(p) for p in ordered],
        subjects=[ticket_ref(issue)],
        signals=Signals(
            verb="Advance the rollout",
            task="advance_the_rollout",
            importance=0 if ready else -1,
            effort="minutes" if ready else "hour",
        ),
    )


def _state_link(pr: _PrView) -> Link:
    """A PR's link, saying where it is: "ready to merge #123", "draft #124", "merged #120"."""
    return Link(text=f"{pr.progress or pr.state} {pr.handle}", url=pr.url or pr.id)


def _abandoned_rollout(config: JiraConfig, issue: JiraIssue, prs: list[_PrView]) -> Item:
    """The item for a ticket in rollout whose PRs were all closed without merging: back to
    work, as nothing of it is out."""
    return Item(
        title=f"{issue.key} {issue.summary}",
        url=issue.url,
        subtitle=f"{issue.status}: all PRs closed without merging",
        details=["nothing of it merged: back to work, or close it"],
        links=[_state_link(p) for p in prs],
        subjects=[ticket_ref(issue)],
        actions=[
            Action(
                name="jira/transition",
                label=f"Move to {targets[0]}",
                fields={"key": issue.key, "to": targets[0]}
                | ({"also": "\n".join(targets[1:])} if targets[1:] else {}),
            )
            for targets in (config.status_in_progress, config.status_closed)
            if targets
        ],
        signals=Signals(
            verb="Take the rollout back",
            task="take_the_rollout_back",
            importance=-1,
            effort="minutes",
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
        links=[_state_link(p) for p in prs],
        subjects=[ticket_ref(issue)],
        actions=[
            Action(
                name="jira/transition",
                label=f"Move to {closed[0]}",
                fields={"key": issue.key, "to": closed[0]}
                | ({"also": "\n".join(closed[1:])} if closed[1:] else {}),
            )
        ]
        if closed
        else [],
        signals=Signals(
            verb="Finish the rollout", task="finish_the_rollout", importance=-1, effort="hour"
        ),
    )


def rollouts(config: JiraConfig, snapshot: JiraSnapshot, graph: Graph) -> Panel:
    """Tickets in a rollout status, with where each of their PRs is: moving on while one is
    open, to close once they've all merged, back to work once they've all closed unmerged."""
    items: list[Item] = []
    for issue in snapshot.issues:
        if not in_status(issue, config.status_rollout):
            continue
        prs = [p for p in graph.view_related(ticket_ref(issue), PR, _PrView) if p.mine]
        if any(p.state == "open" for p in prs):
            items.append(_advance_rollout(issue, prs))
        elif any(p.state == "merged" for p in prs):
            items.append(_finish_rollout(config, issue, prs))
        elif prs:  # every one closed without merging: the rollout has nothing to roll out
            items.append(_abandoned_rollout(config, issue, prs))
    return Panel(
        key="rollouts",
        priority=Band.TRANSITIONS + 1,  # after the transitions
        act_now=True,
        title="Rollouts",
        description="Tickets in a rollout status and where their PRs are",
        items=items,
    )


def transitions(config: JiraConfig, snapshot: JiraSnapshot, graph: Graph) -> Panel:
    items: list[Item] = []
    for issue in snapshot.issues:
        prs = [p for p in graph.view_related(ticket_ref(issue), PR, _PrView) if p.mine]
        open_prs = [p for p in prs if p.state == "open"]
        merged = [p for p in prs if p.state == "merged"]
        closed = [p for p in prs if p.state == "closed"]
        proposal = None
        targets: list[str] = []
        closing = False
        if in_status(issue, config.status_rollout):
            continue  # `rollouts` has it
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
            numbers = ", ".join(p.handle for p in closed)
            proposal = f"PR {numbers} closed unmerged: reopen it or move the ticket back"
            if in_status(issue, config.status_in_review):
                targets = config.status_in_progress
        elif issue.status_category == "new" and open_prs:
            # Only an open PR: as in progress, merged ones don't say the ticket is done.
            targets = config.status_in_progress
            proposal = f"still {issue.status} but has PRs"
        to = targets[0] if targets else None
        if proposal:
            items.append(
                Item(
                    title=f"{issue.key} {issue.summary}",
                    url=issue.url,
                    subtitle=proposal,
                    links=[Link(text=p.handle, url=p.url or p.id) for p in prs],
                    subjects=[ticket_ref(issue)],
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
                        task="transition",
                        effort="minutes",
                        closes=issue.key if closing else None,
                    ),
                    notify=Notify(
                        kind="closeable",
                        text="its PRs are merged: can be closed",
                        thing=issue.key,
                    )
                    if closing and to
                    else None,
                )
            )
    return Panel(
        key="jira_transitions",
        priority=Band.TRANSITIONS,
        act_now=True,
        title="Jira transitions",
        description="Status changes the PR states suggest.",
        items=items,
    )


def end_of(day: date) -> datetime:
    """When a due date runs out: the end of that day where I am, at that day's offset."""
    return localtime.at(day + timedelta(days=1), time())


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
            importance = min(2, (2 if level else 1) + top)
            verb, task = "Look at the incident", "look_at_the_incident"
        elif any(t in kind for t in config.bug_types):
            rank = 2000 + level * 100 + priority * 20
            why = "bug" + (f", escalated ({level})" if level else "")
            why += ", blocked" if blocked else ""
            importance = min(2, (1 if level else 0) + top)
            verb, task = "Fix the bug", "fix_the_bug"
        elif in_status(issue, config.status_rollout):
            rank = -1000  # below blocked work: nothing to do but watch
            why = "in rollout"
            importance = 0
            verb, task = "Watch the rollout", "watch_the_rollout"
        elif not blocked:
            rank = 1000
            why = "unblocked" + (f", under {issue.parent}" if issue.parent else "")
            importance = 0
            verb, task = "Work on it", "work_on_it"
        else:
            rank = 0
            why = (
                "blocked by " + ", ".join(issue.open_blockers) if issue.open_blockers else "blocked"
            )
            importance = 0
            verb, task = "Chase the blockers", "chase_the_blockers"
        rank += min(999, (now - issue.created).days)
        badges: list[str | Badge] = [Badge(text=why, tone="warn") if blocked else why]
        badges += [f"due {issue.due:%b} {issue.due.day}"] if issue.due else []
        items.append(
            Item(
                title=f"{issue.key} {issue.summary}",
                url=issue.url,
                subtitle=" · ".join(
                    part
                    for part in (issue.status, issue.priority, f"{age(now, issue.created)} old")
                    if part
                ),
                badges=badges,
                rank=rank,
                subjects=[ticket_ref(issue)],
                signals=None  # the rollout's own item asks for what's left
                if in_status(issue, config.status_rollout)
                and not any(t in kind for t in (*config.incident_types, *config.bug_types))
                else Signals(
                    verb=verb,
                    task=task,
                    importance=importance,
                    due=end_of(issue.due) if issue.due else None,
                    blocked=blocked,
                    touched=issue.updated,
                ),
            )
        )
    return Panel(
        key="jira_next",
        priority=Band.NEXT,
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
    token_help: ClassVar[str] = (
        "create an API token at https://id.atlassian.com/manage-profile/security/api-tokens,"
        " then store it with `mfd token set jira` (or export it as JIRA_API_TOKEN)"
    )
    title = "Jira"
    init_template = TEMPLATE
    init_example = EXAMPLE
    config_model = JiraConfig
    api_version = API_VERSION
    snapshot_model = JiraSnapshot
    locked_settings: ClassVar[frozenset[str]] = frozenset({"site"})
    """Where its token is sent."""
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        TICKET: KindSpec(JiraTicket, linking=True, fetchable=True, resolvable=True)
    }
    """Tickets link; `linking_patterns` gives the pattern of their keys."""
    notification_kinds: ClassVar[dict[str, str]] = changes.KINDS
    change_rules: ClassVar[Sequence[ChangeRule[Any]]] = changes.RULES
    change_check_seconds: ClassVar[int | None] = 60

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def events(self, req: EventsRequest[JiraConfig, JiraSnapshot]) -> list[Event]:
        """What changed on my tickets. `closeable` is the transitions panel's
        (`Item.notify`)."""
        config, before, after = req.ctx.config, req.before, req.after
        return changes.changes(config, before, after)

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS

    def collect(self, req: CollectRequest[JiraConfig]) -> JiraSnapshot:
        ctx = req.ctx
        return client.collect(ctx.config, ctx.email, ctx.token())

    def store(self, req: StoreRequest[JiraConfig, JiraSnapshot]) -> None:
        ctx, snapshot = req.ctx, req.snapshot
        IssueRepository(ctx.db).replace(snapshot)

    def changed(self, req: ChangedRequest[JiraConfig]) -> Changed:
        """Whether a ticket of mine was updated since the last look (and a minute before
        it, as Jira counts in whole minutes): a one-result search."""
        ctx = req.ctx
        checked = ctx.kv.get(CHECKED)
        found = False
        if checked is not None:
            seconds = (ctx.now - datetime.fromisoformat(checked)).total_seconds()
            minutes = math.ceil(seconds / 60) + 1
            found = client.updated_within(ctx.config, ctx.email, ctx.token(), minutes)
        ctx.kv.set(CHECKED, ctx.now.isoformat())
        return Changed(found, "a ticket of mine was updated")

    def load(self, req: LoadRequest[JiraConfig]) -> JiraSnapshot | None:
        ctx = req.ctx
        return IssueRepository(ctx.db).load()

    def linking_patterns(self, req: LinkingRequest[JiraConfig]) -> dict[str, str | None]:
        """Keys of the projects my tickets have been in, so `UTF-8` or `SHA-256` in a PR
        isn't taken for a ticket; none at all before jira has seen a ticket of mine."""
        ctx = req.ctx
        return {TICKET: key_pattern(IssueRepository(ctx.db).projects())}

    def entities(self, req: EntitiesRequest[JiraConfig, JiraSnapshot]) -> list[Entity]:
        """A `ticket` per issue, with its labels as its tags, except those that say how far it's
        escalated (`escalation_label_prefix`): they aren't what it's about."""
        snapshot, prefix = req.snapshot, req.ctx.config.escalation_label_prefix
        return [
            Entity(
                ref=ticket_ref(issue),
                title=f"{issue.key} {issue.summary}",
                url=issue.url,
                state=issue.status,
                lifecycle="done" if issue.status_category == "done" else "open",
                tags=[label for label in issue.labels if not (prefix and label.startswith(prefix))],
                attrs=attrs(
                    JiraTicket(
                        handle=issue.key,
                        summary=issue.summary,
                        status=issue.status,
                        status_category=issue.status_category,
                        type=issue.type,
                        open_blockers=issue.open_blockers,
                    )
                ),
            )
            for issue in (snapshot.issues if snapshot else [])
        ]

    def fetch(self, req: FetchRequest[JiraConfig]) -> list[Entity]:
        """Tickets by URL, open or resolved, with `resolved_at` (null while open). Their
        projects are remembered, so their keys link from then on (see `linking_patterns`)."""
        ctx, kind, ids = req.ctx, req.kind, req.ids
        keys = [k for id in ids if (k := client.key_of(ctx.config.site, id))]
        if kind != TICKET or not keys:
            return []
        found = client.tickets(ctx.config, ctx.email, keys, ctx.token())
        IssueRepository(ctx.db).remember_projects([i.key for i in found])
        return [
            Entity(
                ref=ticket_ref(i),
                title=f"{i.key} {i.summary}",
                url=i.url,
                state=i.status,
                lifecycle="done" if i.status_category == "done" else "open",
                attrs={"resolved_at": i.resolved.isoformat() if i.resolved else None},
            )
            for i in found
        ]

    def url_for(self, req: UrlRequest[JiraConfig]) -> str | None:
        """A ticket's URL, from its URL or its key alone."""
        ctx, kind, id = req.ctx, req.kind, req.id
        if kind != TICKET:
            return None
        if client.key_of(ctx.config.site, id):
            return id
        return client.ticket_url(ctx.config.site, id) if client.is_key(id) else None

    def handle_for(self, req: HandleRequest[JiraConfig]) -> str | None:
        """A ticket's key, from its URL."""
        ctx, kind, id = req.ctx, req.kind, req.id
        return client.key_of(ctx.config.site, id) if kind == TICKET else None

    def panels(self, req: PanelsRequest[JiraConfig, JiraSnapshot]) -> list[Panel]:
        ctx, snapshot, graph = req.ctx, req.snapshot, req.graph
        if snapshot is None:
            return []
        return [
            transitions(ctx.config, snapshot, graph),
            rollouts(ctx.config, snapshot, graph),
            what_next(ctx.config, snapshot, ctx.now),
        ]

    def act_now(self, req: ActNowRequest[JiraConfig, JiraSnapshot]) -> list[Item]:
        """Its transitions, and the tickets in What next that matter now: important ones
        (incidents, escalated bugs), those due within `req.due_horizon`, and blocked ones, which
        Act now ranks down until they've sat untouched for a while."""
        ctx, panels = req.ctx, req.panels
        nominated = super().act_now(req)
        for panel in panels:
            if panel.key != "jira_next":
                continue
            for item in panel.items:
                signals = item.signals or Signals()
                due_soon = (
                    signals.due is not None
                    and due_pressure(signals.due, ctx.now, req.due_horizon) > 0
                )
                if signals.importance or due_soon or signals.blocked:
                    nominated.append(item)
        return nominated
