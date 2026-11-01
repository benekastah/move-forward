"""The GitHub plugin's panels: reviews for me, my PRs' state, and my teams' PRs.

Pure functions of the snapshot, the derived snapshot and the graph; no I/O.
"""

from datetime import datetime, timedelta

from ...graph import Graph, Ref
from ...page import Item, Link, Panel, age
from .config import GitHubConfig
from .models import DerivedSnapshot, GitHubSnapshot, PullRequest
from .prs import (
    approved,
    changes_requested,
    failing_checks,
    has_conflicts,
    has_ticket_link_line,
    needs_re_review,
    pending_checks,
    ticket_keys,
    unanswered_feedback,
    unresolved_threads,
)

MERGE_QUEUE_LOOKBACK = timedelta(days=2)
PR = "pr"
ENTRY = "entry"
ICED_BRANCH = "iced_branch"
REPO = "repo"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in an `iced_branch` entity's id."""


def is_iced(graph: Graph, repo: str, head: str) -> bool:
    """Whether a PR's branch is on ice: in the checkout of its repo that git knows, or, when
    git knows no checkout of it, in any repo."""
    checkout = graph.get(REPO, repo)
    if checkout is not None:
        iced_id = f"{checkout.attrs.get('dir')}{BRANCH_ID_SEPARATOR}{head}"
        return graph.get(ICED_BRANCH, iced_id) is not None
    return any(e.attrs.get("name") == head for e in graph.of_kind(ICED_BRANCH))


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


def pr_item(
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
        subjects=[pr_ref(pr)],
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

    def _iced(self, pr: PullRequest) -> bool:
        return is_iced(self.graph, pr.repo, pr.head)

    def _entry_attr(self, pr: PullRequest, attr: str) -> list[str]:
        """An attribute's values over the work entries about the PR or its tickets."""
        values: list[str] = []
        for entry in self.graph.related(pr_ref(pr), ENTRY):
            value = entry.attrs.get(attr)
            if isinstance(value, list):
                values += [str(v) for v in value]
        return values

    # Reviews for me

    def reviews_for_me(self) -> Panel:
        items: list[Item] = []
        if self.gh:
            for pr in self.gh.direct_requests:
                items.append(
                    pr_item(
                        self.now, pr, details=["review requested"], rank=pr.updated_at.timestamp()
                    )
                )
            seen = {pr.url for pr in self.gh.direct_requests}
            for pr in self.gh.reviewed_by_me:
                reason = needs_re_review(pr, self.me)
                if reason and pr.url not in seen:
                    items.append(
                        pr_item(self.now, pr, details=[reason], rank=pr.updated_at.timestamp())
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
        for pr in self.mine:
            if pr.is_draft or approved(pr) or pr.requested_users or self._iced(pr):
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
                items.append(pr_item(self.now, pr, details=details, links=links, rank=len(details)))
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
            failing = [c for c in failing_checks(pr) if c.name not in self.config.review_checks]
            details = [f"{c.name}: {c.state}" for c in failing]
            links = [Link(text=c.name, url=c.url) for c in failing if c.url]
            for event in pr.events:
                if event.type == "RemovedFromMergeQueueEvent" and event.at >= cutoff:
                    details.append(
                        f"removed from merge queue {age(self.now, event.at)} ago"
                        + (f": {event.reason}" if event.reason else "")
                    )
            if details:
                items.append(pr_item(self.now, pr, details=details, links=links, rank=len(failing)))
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
                    pr_item(
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
            conditions = self._entry_attr(pr, "merge_conditions")
            if conditions:
                held.append(pr_item(self.now, pr, details=[f"held: {c}" for c in conditions]))
            else:
                ready.append(pr_item(self.now, pr, details=["approved, green, base is default"]))
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
            problems = [label for label in pr.labels if label in self.config.placeholder_labels]
            keys = ticket_keys(pr)
            prefix = self.config.ticket_link_prefix
            if keys and prefix and not has_ticket_link_line(pr.body, prefix):
                problems.append(f"no {prefix} line for {keys[0]}")
            if not pr.body.strip():
                problems.append("empty description")
            if (
                pr.is_draft
                and pr.rollup_state == "SUCCESS"
                and not unresolved_threads(pr)
                and not self._entry_attr(pr, "open_todos")
            ):
                problems.append("draft with green CI and no open todos: ready?")
            if problems:
                items.append(pr_item(self.now, pr, details=problems))
        return Panel(
            key="metadata",
            title="PR housekeeping",
            description="Unknown labels, missing ticket links, drafts that look ready.",
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
                items.append(pr_item(self.now, pr, details=details, rank=rank))
        return Panel(
            key="team_prs",
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
            self.conflicts(),
            self.needs_reviewer(),
            ready,
            held,
            self.team_prs(),
            self.metadata(),
        ]
