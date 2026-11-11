"""The GitHub plugin's panels: reviews for me, my PRs' state, and my teams' PRs.

Pure functions of the snapshot, the derived snapshot and the graph; no I/O.
"""

from collections.abc import Collection
from datetime import datetime, timedelta

from pydantic import Field

from ...api import Action, Badge, Band, Effort, Graph, Item, Link, Model, Panel, Ref, Signals, age
from ...api.kinds import PULL_REQUEST as PR
from ...api.kinds import REVIEW as REVIEW_TASK
from ...api.kinds import TICKET
from .config import GitHubConfig
from .models import DerivedSnapshot, GitHubSnapshot, PullRequest, TeamPrScore
from .prs import (
    BEHIND_REASON,
    STATE_REASONS,
    approved,
    approved_by,
    awaiting_re_review,
    can_be_requested,
    changes_requested,
    failed_runs,
    failing_checks,
    has_conflicts,
    has_ticket_link_line,
    is_bot,
    last_push_at,
    merge_blockers,
    merge_method,
    needs_re_review,
    policy_failures,
    primary_reviewers,
    review_required,
    reviewer_assignment_reasons,
    ticket_keys,
    unanswered_feedback,
    unresolved_threads,
)

ENTRY = "notes.entry"
PRIMARY_REVIEWER = "github/primary_reviewer"
REQUEST_REVIEWER = "github/request_reviewer"
MARK_READY = "github/mark_ready"
UPDATE_BRANCH = "github/update_branch"
RERUN_FAILED = "github/rerun_failed"
MERGE = "github/merge"
AUTO_MERGE = "github/auto_merge"
CANCEL_AUTO_MERGE = "github/cancel_auto_merge"
MERGES_WHEN_READY = "merges when ready"
REQUEST_REVIEWER_TASK = "request_reviewer"
"""The task of Needs a reviewer's items in Act now."""
MAY_BE_READY = "draft with green CI and no open todos: ready?"


class _EntryView(Model):
    """What github reads of a `notes.entry`."""

    merge_conditions: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    """By PR URL."""
    open_todos: list[str] = Field(default_factory=list[str])


REVIEW = "Review"
"""The verb of a review asked of me."""


def pr_ref(pr: PullRequest) -> Ref:
    return Ref(kind=PR, id=pr.url)


def _subtitle(now: datetime, pr: PullRequest) -> str:
    parts = [f"{pr.repo}#{pr.number}", pr.author or "?", f"{age(now, pr.created_at)} old"]
    parts.append(f"updated {age(now, pr.updated_at)} ago")
    return " · ".join(parts)


def _badges(pr: PullRequest) -> list[str | Badge]:
    badges: list[str | Badge] = [f"+{pr.size} lines", f"{pr.changed_files} files"]
    if pr.is_draft:
        badges.insert(0, Badge(text="draft", tone="warn"))
    return badges


def _or(words: list[str]) -> str:
    """ "A", "A or B", "A, B or C"."""
    return f"{', '.join(words[:-1])} or {words[-1]}" if len(words) > 1 else "".join(words)


def _would_cycle(child: str, parent: str, parents: dict[str, str]) -> bool:
    """Whether attaching `child` under `parent` would loop through existing parents."""
    while parent != child and parent in parents:
        parent = parents[parent]
    return parent == child


def pr_item(
    now: datetime,
    pr: PullRequest,
    details: list[str] | None = None,
    links: list[Link] | None = None,
    rank: float = 0,
    signals: Signals | None = None,
    fingerprint: str | None = None,
) -> Item:
    return Item(
        title=pr.title,
        url=pr.url,
        subtitle=_subtitle(now, pr),
        badges=_badges(pr),
        details=details or [],
        links=links or [],
        rank=rank,
        deferred=pr.is_draft,
        subjects=[pr_ref(pr)],
        signals=signals,
        fingerprint=fingerprint,
    )


def _housekeeping_item(now: datetime, pr: PullRequest, problems: list[str]) -> Item:
    """A PR in housekeeping, with its target branch visible beside its usual context."""
    item = pr_item(now, pr, details=problems)
    return item.model_copy(update={"subtitle": f"{item.subtitle} · into {pr.base}"})


def review_effort(lines: int, files: int, config: GitHubConfig) -> Effort:
    """How long reviewing a change takes: minutes within both quick limits, else an hour
    within both hour limits, else a day."""
    if lines <= config.quick_review_max_lines and files <= config.quick_review_max_files:
        return "minutes"
    if lines <= config.hour_review_max_lines and files <= config.hour_review_max_files:
        return "hour"
    return "day"


def requested_of(pr: PullRequest, me: str, team_slugs: Collection[str] = ()) -> datetime | None:
    """When my review was last asked for on the PR, of me by name or of one of my teams
    (`team_slugs`); None if it never was."""
    times = [
        e.at
        for e in pr.events
        if e.type == "ReviewRequestedEvent"
        and (
            (e.reviewer_kind == "user" and e.reviewer == me)
            or (e.reviewer_kind == "team" and e.reviewer in team_slugs)
        )
    ]
    return max(times, default=None)


def _review(
    pr: PullRequest,
    me: str,
    config: GitHubConfig,
    part: TeamPrScore | None = None,
    waiting_since: datetime | None = None,
    team_slugs: Collection[str] = (),
) -> Signals:
    """Review it: its author waits, since my review was asked for (or, for a re-review after
    a push, since the push: `waiting_since`). The effort is that of the part I or my teams
    own (`part`), when there is one."""
    lines, files = (part.part_lines, part.part_files) if part else (pr.size, pr.changed_files)
    return Signals(
        verb=REVIEW,
        task=REVIEW_TASK,
        effort=review_effort(lines, files, config),
        waiting=[pr.author] if pr.author else [],
        waiting_since=waiting_since or requested_of(pr, me, team_slugs),
    )


def merge_conditions(graph: Graph, pr: PullRequest) -> list[str]:
    """The work notes' merge conditions on the PR: what must happen before it merges."""
    entries = graph.view_related(pr_ref(pr), ENTRY, _EntryView)
    return [c for e in entries for c in e.merge_conditions.get(pr.url, [])]


def merge_holds(graph: Graph, pr: PullRequest, ignored: Collection[str]) -> list[str]:
    """Why the PR isn't ready to merge, as Ready to merge decides: GitHub's blockers
    (`merge_blockers`) and the work notes' merge conditions on it. Empty when it's ready."""
    conditions = merge_conditions(graph, pr)
    drafted = ["a draft"] if pr.is_draft else []
    queued = ["in the merge queue"] if pr.in_merge_queue else []
    return [*drafted, *queued, *merge_blockers(pr, ignored), *conditions]


class Builder:
    def __init__(
        self,
        config: GitHubConfig,
        teams: list[str],
        now: datetime,
        snapshot: GitHubSnapshot | None,
        derived: DerivedSnapshot | None,
        graph: Graph,
    ):
        self.config = config
        self.teams = teams
        self.now = now
        self.gh = snapshot
        self.derived = derived
        self.graph = graph
        self.me = snapshot.login if snapshot else ""
        self.mine: list[PullRequest] = snapshot.mine if snapshot else []

    def _entries(self, pr: PullRequest) -> list[_EntryView]:
        """The work entries about the PR or its tickets."""
        return self.graph.view_related(pr_ref(pr), ENTRY, _EntryView)

    def _ticket_keys(self, pr: PullRequest) -> list[str]:
        """The tickets a PR names: Jira's, when the graph finds them by Jira's project keys
        (so `UTF-8` isn't one); else a guess from the text."""
        if not self.graph.links(TICKET):
            return ticket_keys(pr)
        entity = self.graph.get(PR, pr.url)
        refs = [r for r in entity.refs if r.kind == TICKET] if entity else []
        return [self.graph.handle(r.kind, r.id) for r in refs]

    def _team_line(self, pr: PullRequest, score: TeamPrScore) -> str:
        """How much of the PR is my teams': my part (lines and files, as sized for the review)
        when CODEOWNERS gives me some, with how many files are required, and the files in
        directories I work in when there are any."""
        if score.part_files:
            line = f"my part: {score.part_lines} lines in {score.part_files} of {pr.changed_files} files"
        else:
            line = f"{score.owned_files} files owned by my teams"
        if score.required_files:
            line += f" ({score.required_files} required)"
        if score.my_files or not score.part_files:
            line += f", {score.my_files} in dirs I work in"
        return line

    def _slugs(self) -> list[str]:
        """My teams' GitHub slugs."""
        return [self.config.slug(t) for t in self.teams]

    def _part(self, pr: PullRequest) -> TeamPrScore | None:
        """The part of a PR I or my teams own in CODEOWNERS; None when there is none."""
        score = self.derived.team_scores.get(pr.url) if self.derived else None
        return score if score and score.part_files else None

    def _my_part(self, pr: PullRequest) -> list[str]:
        """A detail saying how much of the PR is mine to review, or nothing."""
        part = self._part(pr)
        if part is None:
            return []
        return [
            f"my part: {part.part_lines} lines in {part.part_files} of {pr.changed_files} files"
        ]

    # Reviews for me

    def reviews_for_me(self) -> Panel:
        items: list[Item] = []
        if self.gh:
            for pr in self.gh.direct_requests:
                approved_by_me = approved_by(pr, self.me)
                if pr.is_draft or (approved_by_me and pr.review_decision != "REVIEW_REQUIRED"):
                    continue
                items.append(
                    pr_item(
                        self.now,
                        pr,
                        details=["review requested", *self._my_part(pr)],
                        rank=pr.updated_at.timestamp(),
                        signals=_review(
                            pr, self.me, self.config, self._part(pr), team_slugs=self._slugs()
                        ),
                        fingerprint=pr.head_oid,
                    )
                )
            seen = {item.url for item in items}
            for pr in self.gh.reviewed_by_me:
                approved_by_me = approved_by(pr, self.me)
                reason = needs_re_review(pr, self.me)
                if (
                    pr.is_draft
                    or pr.url in seen
                    or not approved_by_me
                    or pr.review_decision != "REVIEW_REQUIRED"
                    or reason is None
                    or not reason.startswith("pushed")
                ):
                    continue
                items.append(
                    pr_item(
                        self.now,
                        pr,
                        details=[reason, *self._my_part(pr)],
                        rank=pr.updated_at.timestamp(),
                        signals=_review(
                            pr,
                            self.me,
                            self.config,
                            self._part(pr),
                            waiting_since=last_push_at(pr),
                            team_slugs=self._slugs(),
                        ),
                        fingerprint=pr.head_oid,
                    )
                )
                seen.add(pr.url)
        return Panel(
            key="reviews_for_me",
            priority=Band.ASKED,
            act_now=True,
            title="Ready for me to review",
            description="Direct requests and my approvals needing a fresh review.",
            items=items,
            sort_by_importance=True,
        )

    def replies_for_me(self) -> Panel:
        """Mentions that ask me for a reply rather than a PR review."""
        items: list[Item] = []
        if self.gh:
            asked = {
                pr.url
                for pr in (
                    *self.gh.direct_requests,
                    *self.gh.reviewed_by_me,
                    *self.gh.team_requests,
                )
            }
            for pr in self.gh.mentioned:
                if pr.url in asked or pr.is_draft:
                    continue
                items.append(
                    pr_item(
                        self.now,
                        pr,
                        details=["mentions you"],
                        rank=pr.updated_at.timestamp(),
                        signals=Signals(verb="Reply", task="reply", effort="minutes"),
                        fingerprint=pr.head_oid,
                    )
                )
        return Panel(
            key="replies_for_me",
            priority=Band.ASKED,
            act_now=True,
            title="Replies for me",
            description="Open PRs that mention me without requesting my review.",
            items=items,
        )

    # My PRs

    def _assignment_reasons(self, pr: PullRequest) -> list[str]:
        """Reviewer requests still needed, with membership checked for each team."""
        return reviewer_assignment_reasons(
            pr,
            self.config.default_team_reviewer_assignment,
            self.config.team_reviewer_assignment,
            self.gh.team_memberships if self.gh else {},
        )

    def review_stages(self) -> tuple[Panel, Panel]:
        """My PRs needing work before review, then clean ones awaiting review."""
        fixing: list[Item] = []
        awaiting: list[Item] = []
        for pr in self.mine:
            if pr.is_draft or pr.in_merge_queue:
                continue
            waiting_for_review = awaiting_re_review(pr)
            requested_changes_open = changes_requested(pr) and not waiting_for_review
            ci = [c for c in failing_checks(pr) if c.name not in self.config.policy_checks]
            policy = policy_failures(pr, self.config.policy_checks)
            threads = unresolved_threads(pr, self.config.ignored_reviewers)
            unanswered = unanswered_feedback(pr, self.me, self.config.ignored_reviewers)
            assignment = self._assignment_reasons(pr)
            reasons = [
                *(["CI failing: " + ", ".join(c.name for c in ci)] if ci else []),
                *(["policy failing: " + ", ".join(c.name for c in policy)] if policy else []),
                *(["merge conflict"] if has_conflicts(pr) else []),
                *([f"{len(threads)} unresolved review thread(s)"] if threads else []),
                *(["changes requested"] if requested_changes_open else []),
                *unanswered,
                *assignment,
            ]
            if reasons:
                item = pr_item(self.now, pr, details=reasons, rank=-pr.updated_at.timestamp())
                item = self._with_rerun(item, pr)
                fixing.append(self._with_reviewer_field(item, pr) if assignment else item)
            elif not approved(pr) and (
                review_required(pr) or pr.requested_users or pr.requested_teams
            ):
                who = [*pr.requested_users, *(f"team {team}" for team in pr.requested_teams)]
                item = pr_item(
                    self.now,
                    pr,
                    details=["waiting on " + ", ".join(who)],
                    rank=-pr.updated_at.timestamp(),
                )
                awaiting.append(self._with_auto_merge(item, pr))
        return (
            Panel(
                key="fix_before_review",
                priority=Band.MINE,
                title="Fix before review",
                description="My open PRs with failing checks, conflicts, feedback or missing reviewers.",
                items=fixing,
                sort_by_importance=True,
            ),
            Panel(
                key="ready_for_review",
                priority=Band.WAITING,
                title="Ready for review",
                description="My clean PRs awaiting approval; pending checks do not hold them back.",
                items=awaiting,
            ),
        )

    def needs_reviewer(self) -> Panel:
        items: list[Item] = []
        ask = Signals(verb="Request a reviewer", task=REQUEST_REVIEWER_TASK, effort="minutes")
        for pr in self.mine:
            if pr.is_draft or pr.in_merge_queue:
                continue
            if not (details := self._assignment_reasons(pr)):
                continue
            # Listed either way, as the teams' rules still say; but an approved PR isn't waiting
            # on anyone, and asking can't answer a team whose members can't be read.
            signals = ask if can_be_requested(details) and not approved(pr) else None
            # Those asked since the suggestions were worked out are left out: logins ignore case.
            asked = {login.lower() for login in pr.requested_users}
            suggested = self.derived.suggestions.get(pr.url, []) if self.derived else []
            if suggestions := [s for s in suggested if s.login.lower() not in asked]:
                details.append(
                    "recent committers here: "
                    + ", ".join(f"{s.login} ({s.commits})" for s in suggestions)
                )
            item = pr_item(
                self.now, pr, details=details, rank=-pr.created_at.timestamp(), signals=signals
            )
            items.append(self._with_reviewer_field(item, pr))
        return Panel(
            key="needs_reviewer",
            priority=Band.MINE,
            act_now=True,
            title="Needs a reviewer",
            description="My open PRs missing a reviewer request under their teams' rules.",
            items=items,
        )

    def feedback(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            threads = unresolved_threads(pr, self.config.ignored_reviewers)
            details = [
                f"{'(outdated) ' if t.outdated else ''}{t.author or '?'} on {t.path or 'PR'}:"
                f" {t.body.strip().splitlines()[0][:120] if t.body.strip() else ''}"
                for t in threads
            ]
            links = [Link(text=f"thread {n}", url=t.url) for n, t in enumerate(threads, 1) if t.url]
            if changes_requested(pr) and not awaiting_re_review(pr):
                details.insert(0, "changes requested")
            details += unanswered_feedback(pr, self.me, self.config.ignored_reviewers)
            if details:
                authors = list(
                    dict.fromkeys(t.author for t in threads if t.author and not is_bot(t.author))
                )
                since = min((t.created_at for t in threads if t.created_at), default=None)
                waiting = Signals(
                    verb="Reply",
                    task="reply",
                    effort="minutes",
                    waiting=authors,
                    waiting_since=since,
                )
                items.append(
                    pr_item(
                        self.now,
                        pr,
                        details=details,
                        links=links,
                        rank=len(details),
                        signals=waiting,
                    )
                )
        return Panel(
            key="feedback",
            priority=Band.MINE,
            act_now=True,
            title="Feedback to address",
            description="Unresolved review threads, changes requested, and comments since my last push.",
            items=items,
        )

    def ci_failures(self) -> Panel:
        items: list[Item] = []
        cutoff = self.now - timedelta(days=self.config.merge_queue_eviction_days)
        for pr in self.mine:
            if pr.is_draft:
                continue
            failing = [c for c in failing_checks(pr) if c.name not in self.config.policy_checks]
            details = [f"{c.name}: {c.state}" for c in failing]
            links = [Link(text=c.name, url=c.url) for c in failing if c.url]
            requeued = max(
                (e.at for e in pr.events if e.type == "AddedToMergeQueueEvent"), default=None
            )
            for event in pr.events:
                if (
                    event.type == "RemovedFromMergeQueueEvent"
                    and event.at >= cutoff
                    and not pr.in_merge_queue
                    and not (requeued and requeued > event.at)
                    and pr.rollup_state != "SUCCESS"
                ):
                    details.append(
                        f"removed from merge queue {age(self.now, event.at)} ago"
                        + (f": {event.reason}" if event.reason else "")
                    )
            if details:
                item = pr_item(self.now, pr, details=details, links=links, rank=len(failing))
                items.append(self._with_rerun(item, pr))
        return Panel(
            key="ci_failures",
            priority=Band.MINE,
            act_now=True,
            signals=Signals(verb="Fix CI", task="fix_ci", effort="hour"),
            title="CI failures",
            description="Failing checks and recent merge-queue evictions on my non-draft PRs.",
            items=items,
        )

    def policy(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            if pr.is_draft:
                continue
            failing = policy_failures(pr, self.config.policy_checks)
            if failing:
                details = [f"{c.name}: {c.state}" for c in failing]
                links = [Link(text=c.name, url=c.url) for c in failing if c.url]
                items.append(pr_item(self.now, pr, details=details, links=links, rank=len(failing)))
        return Panel(
            key="policy",
            priority=Band.MINE,
            act_now=True,
            signals=Signals(verb="Meet PR policy", task="meet_pr_policy", effort="minutes"),
            title="PR policy",
            description="Failing policy checks on my non-draft PRs: a label, an assignee or the"
            " like is missing.",
            items=items,
        )

    def conflicts(self) -> Panel:
        items: list[Item] = []
        my_heads = {(pr.repo, pr.head) for pr in self.mine}
        for pr in self.mine:
            if pr.is_draft or not has_conflicts(pr):
                continue
            details = [f"mergeable: {pr.mergeable}, state: {pr.merge_state}"]
            signals = None
            # Stacked on another of my branches: restacking fixes it.
            if (pr.repo, pr.base) in my_heads:
                details.insert(0, f"stacked on {pr.base}: restack (git stack sync)")
                signals = Signals(
                    verb="Restack the stack", task="restack_the_stack", effort="minutes"
                )
            items.append(pr_item(self.now, pr, details=details, signals=signals))
        return Panel(
            key="conflicts",
            priority=Band.MINE,
            act_now=True,
            signals=Signals(verb="Resolve conflicts", task="resolve_conflicts", effort="hour"),
            title="Merge conflicts",
            description="My open, non-draft PRs that conflict with their base.",
            items=items,
        )

    def ready_to_merge(self) -> tuple[Panel, Panel]:
        ready: list[Item] = []
        held: list[Item] = []
        for pr in self.mine:
            if pr.is_draft or pr.in_merge_queue:
                continue
            reasons = merge_blockers(pr, self.config.ignored_reviewers)
            if reasons and all(r in STATE_REASONS for r in reasons):
                item = pr_item(self.now, pr, details=[f"held: {r}" for r in reasons])
                held.append(
                    self._with_update_button(item, pr) if BEHIND_REASON in reasons else item
                )
            if reasons:
                continue
            # The rule Merge goes by: listed here only when nothing holds it.
            if holds := merge_holds(self.graph, pr, self.config.ignored_reviewers):
                held.append(pr_item(self.now, pr, details=[f"held: {h}" for h in holds]))
            else:
                merge = Signals(
                    verb="Merge", task="merge", effort="minutes", closes=f"{pr.repo}#{pr.number}"
                )
                item = pr_item(
                    self.now, pr, details=["approved, green, base is default"], signals=merge
                )
                ready.append(self._with_merge_button(item, pr))
        return (
            Panel(
                key="ready_to_merge",
                priority=Band.MINE,
                act_now=True,
                title="Ready to merge",
                description="Approved, green, no open threads, based on the default branch.",
                items=ready,
            ),
            Panel(
                key="held",
                priority=Band.WAITING,
                title="Held by merge conditions",
                description="Would be ready, but the work notes hold them, or GitHub says the branch is"
                " behind or blocked.",
                items=held,
            ),
        )

    def merge_queue(self) -> Panel:
        return Panel(
            key="merge_queue",
            priority=Band.WAITING,
            title="In the merge queue",
            description="My PRs waiting in the merge queue.",
            items=[
                pr_item(self.now, pr, details=["in the merge queue"])
                for pr in self.mine
                if pr.in_merge_queue
            ],
        )

    def metadata(self) -> Panel:
        items: dict[str, Item] = {}
        prs = {pr.url: pr for pr in self.mine}
        by_head = {(pr.repo, pr.head): pr for pr in self.mine}
        for pr in self.mine:
            problems = [label for label in pr.labels if label in self.config.placeholder_labels]
            keys = self._ticket_keys(pr)
            prefixes = self.config.ticket_link_prefixes
            if keys and prefixes and not has_ticket_link_line(pr.body, prefixes):
                problems.append(f"no {_or(prefixes)} line for {keys[0]}")
            if not pr.body.strip():
                problems.append("empty description")
            idle = self.now - pr.updated_at
            if (
                pr.is_draft
                and self.config.stale_draft_days
                and idle >= timedelta(days=self.config.stale_draft_days)
            ):
                problems.append(f"stale draft: untouched {age(self.now, pr.updated_at)}")
            if size_label := next((x for x in pr.labels if x in self.config.split_pr_labels), None):
                problems.append(f"worth splitting: labeled {size_label}")
            elif pr.size >= self.config.split_pr_lines:
                problems.append(f"worth splitting: {pr.size} lines")
            elif pr.size >= self.config.split_pr_suggest_lines:
                problems.append(f"may need splitting: {pr.size} lines")
            may_be_ready = (
                pr.is_draft
                and pr.rollup_state == "SUCCESS"
                and not unresolved_threads(pr, self.config.ignored_reviewers)
                and not any(e.open_todos for e in self._entries(pr))
            )
            if may_be_ready:
                problems.append(MAY_BE_READY)
            if problems or pr.is_draft:
                item = _housekeeping_item(self.now, pr, problems)
                if may_be_ready:
                    ready = Action(
                        name=MARK_READY,
                        label="Ready for review",
                        fields={"pr": pr.url},
                        confirm=f"Mark {pr.title} ready for review? Requested reviewers and"
                        " code owners are notified.",
                    )
                    item = item.model_copy(update={"actions": [*item.actions, ready]})
                items[pr.url] = item

        # Include parents even when they have no housekeeping flags, so a draft's
        # target branch has a visible place in the tree.
        parents: dict[str, str] = {}
        for url in list(items):
            child = prs[url]
            seen = {url}
            while (parent := by_head.get((child.repo, child.base))) is not None:
                if parent.url in seen or _would_cycle(child.url, parent.url, parents):
                    break
                seen.add(parent.url)
                items.setdefault(
                    parent.url,
                    _housekeeping_item(self.now, parent, []),
                )
                parents[child.url] = parent.url
                child = parent

        descendants: dict[str, list[str]] = {}
        for child, parent in parents.items():
            descendants.setdefault(parent, []).append(child)
            items[child] = items[child].model_copy(update={"parent_anchor": items[parent].anchor})

        def has_open(url: str) -> bool:
            return not prs[url].is_draft or any(
                has_open(child) for child in descendants.get(url, [])
            )

        for url, item in items.items():
            if url in descendants:
                items[url] = item.model_copy(
                    update={
                        "children_open": has_open(url),
                        "children_title": "PRs based on this branch",
                    }
                )
        return Panel(
            key="metadata",
            priority=Band.HOUSEKEEPING,
            title="PR housekeeping",
            description="Draft branch trees, unknown labels, missing ticket links, big PRs.",
            items=list(items.values()),
        )

    # Team

    def team_prs(self) -> Panel:
        items: list[Item] = []
        if self.gh:
            direct = {pr.url for pr in self.gh.direct_requests}
            scores = self.derived.team_scores if self.derived else {}
            for pr in self.gh.team_requests:
                if pr.url in direct or pr.is_draft:
                    continue
                if approved(pr):
                    # GitHub's decision (or, where no review is required, anyone's approval)
                    # says the review needed is in, whatever its request on my team says.
                    continue
                score = scores.get(pr.url)
                details: list[str] = []
                rank = 0.0
                if score:
                    details.append(self._team_line(pr, score))
                    rank = score.required_files * 100 + score.owned_files + score.my_files * 10
                items.append(pr_item(self.now, pr, details=details, rank=rank))
        return Panel(
            key="team_prs",
            priority=Band.ASKED,
            title="Requested from my teams",
            description=f"PRs waiting on {', '.join(self.teams) or 'my teams'}, ranked by"
            " CODEOWNERS and the paths I work in.",
            items=items,
        )

    def _with_auto_merge(self, item: Item, pr: PullRequest) -> Item:
        """The PR's item with Merge when ready where its repo allows auto-merge, how it'd merge
        is known, and its base requires a review it hasn't had: auto-merge waits only for what
        GitHub requires, so without that it wouldn't wait for anyone to look. Not
        while the work notes hold it, nor once auto-merge is on (see `_auto_merging`)."""
        method = merge_method(pr, self.config.merge.method)
        if (
            pr.auto_merge
            or not pr.auto_merge_allowed
            or pr.review_decision != "REVIEW_REQUIRED"
            or (method is None and not pr.merge_queue)
            or merge_conditions(self.graph, pr)
        ):
            return item
        how = "its merge queue" if pr.merge_queue else f"merging ({(method or '').lower()})"
        button = Action(
            name=AUTO_MERGE,
            label="Merge when ready",
            fields={"pr": pr.url, "head": pr.head_oid},
            confirm=f"Let {pr.repo}#{pr.number} merge by itself, by {how}, once it has what"
            f" {pr.base} requires (an approval, green checks), at {pr.head_oid[:7]}? A push"
            " after this isn't merged unseen.",
        )
        return item.model_copy(update={"actions": [*item.actions, button]})

    def _auto_merging(self, panel: Panel) -> Panel:
        """The panel with, on every item about one of my PRs with auto-merge on, a badge saying
        so and Cancel auto-merge, wherever it's shown; a warning one while the work notes hold
        it, as GitHub merges it regardless."""
        prs = {pr.url: pr for pr in self.mine if pr.auto_merge}

        def marked(item: Item) -> Item:
            pr = prs.get(item.url or "")
            if pr is None:
                return item
            held = merge_conditions(self.graph, pr)
            badge = (
                Badge(text=f"{MERGES_WHEN_READY}, though held", tone="warn")
                if held
                else Badge(text=MERGES_WHEN_READY, tone="good")
            )
            cancel = Action(
                name=CANCEL_AUTO_MERGE, label="Cancel auto-merge", fields={"pr": pr.url}
            )
            return item.model_copy(
                update={"badges": [*item.badges, badge], "actions": [*item.actions, cancel]}
            )

        return panel.model_copy(update={"items": [marked(item) for item in panel.items]})

    def _with_merge_button(self, item: Item, pr: PullRequest) -> Item:
        """The PR's item with Add to queue where its base has a merge queue, else Merge with the
        method it'd use; neither while how its repo merges isn't known yet."""
        method = merge_method(pr, self.config.merge.method)
        fields = {"pr": pr.url, "head": pr.head_oid}
        if pr.merge_queue:
            button = Action(
                name=MERGE,
                label="Add to queue",
                fields=fields,
                confirm=f"Add {pr.repo}#{pr.number} to {pr.base}'s merge queue at"
                f" {pr.head_oid[:7]}? The queue merges it once its checks pass.",
            )
        elif method is not None:
            button = Action(
                name=MERGE,
                label="Merge",
                fields=fields,
                confirm=f"Merge {pr.repo}#{pr.number} into {pr.base} ({method.lower()}) at"
                f" {pr.head_oid[:7]}?",
            )
        else:
            return item
        return item.model_copy(update={"actions": [*item.actions, button]})

    def _with_update_button(self, item: Item, pr: PullRequest) -> Item:
        """The PR's item with a button to bring its branch up to date with its base."""
        update = Action(
            name=UPDATE_BRANCH,
            label="Update branch",
            fields={"pr": pr.url},
            confirm=f"Merge {pr.base} into {pr.head} on GitHub? Pull it here afterwards. Comments"
            " from before it stop counting as unanswered, as with any push.",
        )
        return item.model_copy(update={"actions": [*item.actions, update]})

    def _with_rerun(self, item: Item, pr: PullRequest) -> Item:
        """The PR's item with Rerun failed, when it has a failed Actions run that's over."""
        if not failed_runs(self.config.host, pr, self.config.policy_checks):
            return item
        rerun = Action(name=RERUN_FAILED, label="Rerun failed", fields={"pr": pr.url})
        return item.model_copy(update={"actions": [*item.actions, rerun]})

    # Picking reviewers

    def _reviewers_to_suggest(self, pr: PullRequest, leaving_out: Collection[str]) -> list[str]:
        """Whom to offer as the PR's reviewer, likeliest first: those asked to review it, the
        members of the teams asked (as far as they're known), then recent committers to what
        it changes; never its author, or anyone in `leaving_out`."""
        owner = pr.repo.partition("/")[0]
        memberships = self.gh.team_memberships if self.gh else {}
        suggested = self.derived.suggestions.get(pr.url, []) if self.derived else []
        logins = [
            *pr.requested_users,
            *(m for team in pr.requested_teams for m in memberships.get(f"{owner}/{team}", [])),
            *(s.login for s in suggested),
        ]
        chosen = {login.lower() for login in (pr.author, *leaving_out) if login}  # ignoring case
        return [login for login in dict.fromkeys(logins) if login.lower() not in chosen]

    def _with_reviewer_field(self, item: Item, pr: PullRequest) -> Item:
        """The PR's item with a field to ask someone to review it."""
        field = Action(
            name=REQUEST_REVIEWER,
            label="Request",
            fields={"pr": pr.url},
            input="Reviewer",
            suggestions=self._reviewers_to_suggest(pr, pr.requested_users),
        )
        return item.model_copy(update={"actions": [*item.actions, field]})

    def _primary_reviewer(self, item: Item, pr: PullRequest) -> Item:
        """The PR's item with who its primary reviewer is, and a field to pick another."""
        current = primary_reviewers(pr)
        field = Action(
            name=PRIMARY_REVIEWER,
            label="Change primary" if current else "Set primary",
            fields={"pr": pr.url},
            input="Primary reviewer",
            suggestions=self._reviewers_to_suggest(pr, current),
        )
        badges = [f"primary: {', '.join(current)}"] if current else []
        return item.model_copy(
            update={"badges": [*item.badges, *badges], "actions": [*item.actions, field]}
        )

    def _with_primary_reviewers(self, panel: Panel) -> Panel:
        """The panel with `_primary_reviewer` on the items about my PRs in repos that have
        primary reviewers, unless they're in the merge queue, past needing one."""
        prs = {
            pr.url: pr
            for pr in self.mine
            if self.config.has_primary_reviewer(pr.repo) and not pr.in_merge_queue
        }
        return panel.model_copy(
            update={
                "items": [
                    self._primary_reviewer(item, prs[item.url]) if item.url in prs else item
                    for item in panel.items
                ]
            }
        )

    def panels(self) -> list[Panel]:
        fixing, awaiting = self.review_stages()
        ready, held = self.ready_to_merge()
        panels = [
            fixing,
            awaiting,
            self.reviews_for_me(),
            self.replies_for_me(),
            self.feedback(),
            self.ci_failures(),
            self.policy(),
            self.conflicts(),
            self.needs_reviewer(),
            ready,
            held,
            self.merge_queue(),
            self.team_prs(),
            self.metadata(),
        ]
        return _for_act_now(
            [self._auto_merging(self._with_primary_reviewers(panel)) for panel in panels]
        )


def _for_act_now(panels: list[Panel]) -> list[Panel]:
    """`panels` with what Act now should take of them: the feedback on my drafts too, though a
    draft's item is deferred, but not a deferred review, as a draft isn't ready for one. And
    asking for a reviewer loses its effort while there's something else to do on the PR, which
    is worth doing before anyone looks at it, so it ranks below that and shows as "also" until
    then. What Act now takes is what's on the panels (`Panel.candidate`), so that's where the
    effort goes: such an item carries its signals itself, `effort` "unknown", and `mfd dump`
    shows them so, as what Act now ranks it by."""

    def task(panel: Panel, item: Item) -> str | None:
        signals = item.signals or panel.signals
        return signals.task if signals else None

    flagged = [
        panel.model_copy(
            update={
                "items": [
                    item.model_copy(update={"act_now": True})
                    if panel.act_now
                    and item.deferred
                    and task(panel, item) not in (None, REVIEW_TASK)
                    else item
                    for item in panel.items
                ]
            }
        )
        for panel in panels
    ]
    busy = {
        item.url
        for panel in flagged
        for item in panel.items
        if panel.candidate(item) and task(panel, item) not in (None, REQUEST_REVIEWER_TASK)
    }

    def waiting(panel: Panel, item: Item) -> Item:
        signals = item.signals or panel.signals
        if signals is None or signals.task != REQUEST_REVIEWER_TASK or item.url not in busy:
            return item
        return item.model_copy(update={"signals": signals.model_copy(update={"effort": "unknown"})})

    return [
        panel.model_copy(update={"items": [waiting(panel, item) for item in panel.items]})
        for panel in flagged
    ]
