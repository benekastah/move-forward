"""The GitHub plugin's panels: reviews for me, my PRs' state, and my teams' PRs.

Pure functions of the snapshot, the derived snapshot and the graph; no I/O.
"""

from collections.abc import Collection
from datetime import datetime, timedelta

from pydantic import Field

from ...domain import Model
from ...graph import Graph, Ref
from ...page import Effort, Item, Link, Panel, Signals, age
from .config import GitHubConfig
from .models import DerivedSnapshot, GitHubSnapshot, PullRequest, TeamPrScore
from .prs import (
    STATE_REASONS,
    approved,
    changes_requested,
    failing_checks,
    has_conflicts,
    has_ticket_link_line,
    is_bot,
    last_push_at,
    merge_blockers,
    needs_re_review,
    review_required,
    ticket_keys,
    unanswered_feedback,
    unresolved_threads,
)

PR = "pr"
ENTRY = "entry"


class _EntryView(Model):
    """What github reads of an `entry` (notes')."""

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


def _badges(pr: PullRequest) -> list[str]:
    badges = [f"+{pr.size} lines", f"{pr.changed_files} files"]
    if pr.is_draft:
        badges.insert(0, "draft")
    return badges


def _or(words: list[str]) -> str:
    """ "A", "A or B", "A, B or C"."""
    return f"{', '.join(words[:-1])} or {words[-1]}" if len(words) > 1 else "".join(words)


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
        draft=pr.is_draft,
        subjects=[pr_ref(pr)],
        signals=signals,
        fingerprint=fingerprint,
    )


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
        effort=review_effort(lines, files, config),
        waiting=[pr.author] if pr.author else [],
        waiting_since=waiting_since or requested_of(pr, me, team_slugs),
    )


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
        if not self.graph.links("ticket"):
            return ticket_keys(pr)
        entity = self.graph.get(PR, pr.url)
        return [r.id for r in entity.refs if r.kind == "ticket"] if entity else []

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
            seen = {pr.url for pr in self.gh.direct_requests}
            for pr in self.gh.reviewed_by_me:
                reason = needs_re_review(pr, self.me)
                if reason and pr.url not in seen:
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
                                waiting_since=last_push_at(pr)
                                if reason.startswith("pushed")
                                else None,
                                team_slugs=self._slugs(),
                            ),
                            fingerprint=pr.head_oid,
                        )
                    )
            asked = {pr.url for pr in (*self.gh.direct_requests, *self.gh.reviewed_by_me)}
            for pr in self.gh.mentioned:
                if pr.url not in asked and not pr.is_draft:
                    items.append(
                        pr_item(
                            self.now,
                            pr,
                            details=["mentions you"],
                            rank=pr.updated_at.timestamp(),
                            signals=Signals(verb="Reply", effort="minutes"),
                            fingerprint=pr.head_oid,
                        )
                    )
        return Panel(
            key="reviews_for_me",
            priority=10,  # 1. reviews asked of me
            act_now=True,
            snoozable=True,
            title="Reviews for me",
            description="PRs asking for my review directly, that changed since I reviewed them, or"
            " that mention me.",
            items=items,
        )

    # My PRs

    def needs_reviewer(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            if pr.is_draft or approved(pr) or not review_required(pr) or pr.requested_users:
                continue
            details: list[str] = []
            if pr.requested_teams:
                details.append("requested from team: " + ", ".join(pr.requested_teams))
            else:
                details.append("no reviewers requested")
            if self.derived and (suggestions := self.derived.suggestions.get(pr.url)):
                details.append(
                    "recent committers here: "
                    + ", ".join(f"{s.login} ({s.commits})" for s in suggestions)
                )
            items.append(pr_item(self.now, pr, details=details, rank=-pr.created_at.timestamp()))
        return Panel(
            key="needs_reviewer",
            priority=80,  # 8a. a person for each review
            title="Needs a reviewer",
            description="My open PRs with no person requested and no approval yet.",
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
            if changes_requested(pr):
                details.insert(0, "changes requested")
            details += unanswered_feedback(pr, self.me, self.config.ignored_reviewers)
            if details:
                authors = list(
                    dict.fromkeys(t.author for t in threads if t.author and not is_bot(t.author))
                )
                since = min((t.created_at for t in threads if t.created_at), default=None)
                waiting = Signals(
                    verb="Reply", effort="minutes", waiting=authors, waiting_since=since
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
            priority=30,  # 3. feedback on my PRs
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
                items.append(pr_item(self.now, pr, details=details, links=links, rank=len(failing)))
        return Panel(
            key="ci_failures",
            priority=40,  # 4. CI failures
            act_now=True,
            signals=Signals(verb="Fix CI", effort="hour"),
            title="CI failures",
            description="Failing checks and recent merge-queue evictions on my non-draft PRs.",
            items=items,
        )

    def policy(self) -> Panel:
        items: list[Item] = []
        for pr in self.mine:
            if pr.is_draft:
                continue
            failing = [c for c in failing_checks(pr) if c.name in self.config.policy_checks]
            if failing:
                details = [f"{c.name}: {c.state}" for c in failing]
                links = [Link(text=c.name, url=c.url) for c in failing if c.url]
                items.append(pr_item(self.now, pr, details=details, links=links, rank=len(failing)))
        return Panel(
            key="policy",
            priority=45,  # 4. CI failures, but quick
            act_now=True,
            signals=Signals(verb="Meet PR policy", effort="minutes"),
            title="PR policy",
            description="Failing policy checks on my non-draft PRs: a label, an assignee or the"
            " like is missing.",
            items=items,
        )

    def conflicts(self) -> Panel:
        items: list[Item] = []
        my_heads = {pr.head for pr in self.mine}
        for pr in self.mine:
            if pr.is_draft or not has_conflicts(pr):
                continue
            details = [f"mergeable: {pr.mergeable}, state: {pr.merge_state}"]
            signals = None
            if pr.base in my_heads:  # stacked on another of my branches: restacking fixes it
                details.insert(0, f"stacked on {pr.base}: restack (git stack sync)")
                signals = Signals(verb="Restack the stack", effort="minutes")
            items.append(pr_item(self.now, pr, details=details, signals=signals))
        return Panel(
            key="conflicts",
            priority=20,  # 2. sync stacks
            act_now=True,
            signals=Signals(verb="Resolve conflicts", effort="hour"),
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
                held.append(pr_item(self.now, pr, details=[f"held: {r}" for r in reasons]))
            if reasons:
                continue
            conditions = [c for e in self._entries(pr) for c in e.merge_conditions.get(pr.url, [])]
            if conditions:
                held.append(pr_item(self.now, pr, details=[f"held: {c}" for c in conditions]))
            else:
                merge = Signals(verb="Merge", effort="minutes", closes=f"{pr.repo}#{pr.number}")
                ready.append(
                    pr_item(
                        self.now, pr, details=["approved, green, base is default"], signals=merge
                    )
                )
        return (
            Panel(
                key="ready_to_merge",
                priority=90,  # 9. the merge queue
                act_now=True,
                title="Ready to merge",
                description="Approved, green, no open threads, based on the default branch.",
                items=ready,
            ),
            Panel(
                key="held",
                priority=91,  # 9, with merge conditions
                title="Held by merge conditions",
                description="Would be ready, but the work notes hold them, or GitHub says the branch is"
                " behind or blocked.",
                items=held,
            ),
        )

    def merge_queue(self) -> Panel:
        return Panel(
            key="merge_queue",
            priority=92,  # 9, after ready to merge and held
            title="In the merge queue",
            description="My PRs waiting in the merge queue.",
            items=[
                pr_item(self.now, pr, details=["in the merge queue"])
                for pr in self.mine
                if pr.in_merge_queue
            ],
        )

    def metadata(self) -> Panel:
        items: list[Item] = []
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
            if (
                pr.is_draft
                and pr.rollup_state == "SUCCESS"
                and not unresolved_threads(pr, self.config.ignored_reviewers)
                and not any(e.open_todos for e in self._entries(pr))
            ):
                problems.append("draft with green CI and no open todos: ready?")
            if problems:
                items.append(pr_item(self.now, pr, details=problems))
        return Panel(
            key="metadata",
            priority=70,  # 7. PR metadata
            title="PR housekeeping",
            description="Unknown labels, missing ticket links, drafts that look ready, big PRs.",
            items=items,
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
            priority=81,  # 8a, for my teams' PRs
            title="Requested from my teams",
            description=f"PRs waiting on {', '.join(self.teams) or 'my teams'}, ranked by"
            " CODEOWNERS and the paths I work in.",
            items=items,
        )

    def panels(self) -> list[Panel]:
        ready, held = self.ready_to_merge()
        return [
            self.reviews_for_me(),
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
