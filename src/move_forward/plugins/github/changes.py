"""What changed between two GitHub snapshots, as notification events (`Plugin.events`).

Each kind says something once, when a state changes; a PR the earlier snapshot didn't hold
has no state to compare, so it only ever says it's new (a review requested of me).
"""

from ...notifications import Event
from ...page import anchor
from .models import GitHubSnapshot, PullRequest, Review, TimelineEvent
from .panels import PR

KINDS = {
    "pr_mergeable": "my PR is approved, green and has no conflicts",
    "ci_failed": "CI fails on my PR",
    "ci_recovered": "CI passes again on my PR",
    "review_received": "someone reviews my PR (approved, changes requested, commented)",
    "conversation_comment": "someone comments on my PR's conversation",
    "merge_conflict": "my PR gets a merge conflict",
    "pr_merged": "my PR is merged, by anyone",
    "pr_closed": "my PR is closed without merging",
    "merge_queue_added": "someone else adds my PR to the merge queue",
    "merge_queue_removed": "my PR leaves the merge queue without merging",
    "thread_reply": "a review thread on my PR or one I reviewed gets a reply",
    "review_requested_me": "a review is requested of me",
    "review_requested_team": "a review is requested of my team",
    "new_push": "new code is pushed to a PR I reviewed",
    "author_replied": "the author comments on a PR I reviewed",
}
"""The kinds, with what each says, for the settings page."""

FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
"""Check states that count as the check failing."""
REVIEW_TEXT = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "requested changes",
    "COMMENTED": "reviewed",
}
"""What a review says, by its state; a dismissed one says nothing."""


def _event(pr: PullRequest, kind: str, text: str) -> Event:
    return Event(
        kind=kind,
        thing=pr.url,
        title=f"{pr.repo}#{pr.number} {pr.title}",
        text=text,
        anchor=anchor(PR, pr.url),
    )


def _human(login: str | None, me: str) -> str | None:
    """`login` unless it's me or a bot."""
    return login if login and login != me and not login.endswith("[bot]") else None


def ready(pr: PullRequest) -> bool:
    """Approved, green and conflict-free, and not yet queued: ready to merge."""
    return (
        not pr.is_draft
        and not pr.in_merge_queue
        and pr.review_decision == "APPROVED"
        and pr.rollup_state == "SUCCESS"
        and pr.mergeable == "MERGEABLE"
    )


def _failing(pr: PullRequest) -> bool:
    return pr.rollup_state in FAILED


def _new_events(pr: PullRequest, old: PullRequest, kind: str) -> list[TimelineEvent]:
    """The timeline events of `kind` that `pr` has and `old` hadn't."""
    seen = {(e.at, e.actor) for e in old.events if e.type == kind}
    return [e for e in pr.events if e.type == kind and (e.at, e.actor) not in seen]


def _new_reviews(pr: PullRequest, old: PullRequest, me: str) -> list[Review]:
    seen = {(r.author, r.state, r.submitted_at) for r in old.reviews}
    return [
        r
        for r in pr.reviews
        if (r.author, r.state, r.submitted_at) not in seen
        and r.state in REVIEW_TEXT
        and _human(r.author, me)
    ]


def _replies(pr: PullRequest, old: PullRequest) -> int:
    """How many comments the threads `old` knew have gained."""
    counts = {t.url: t.comment_count for t in old.threads if t.url}
    return sum(max(0, t.comment_count - counts[t.url]) for t in pr.threads if t.url in counts)


def _mine(pr: PullRequest, old: PullRequest, me: str) -> list[Event]:
    """What changed on one of my PRs that's still open."""
    found: list[Event] = []
    if ready(pr) and not ready(old):
        found.append(_event(pr, "pr_mergeable", "can be merged"))
    if _failing(pr) and not _failing(old):
        names = ", ".join(c.name for c in pr.checks if c.state in FAILED)
        found.append(_event(pr, "ci_failed", f"CI failed: {names}" if names else "CI failed"))
    if pr.rollup_state == "SUCCESS" and _failing(old):
        found.append(_event(pr, "ci_recovered", "CI passes again"))
    if pr.mergeable == "CONFLICTING" and old.mergeable != "CONFLICTING":
        found.append(_event(pr, "merge_conflict", "has a merge conflict"))
    for review in _new_reviews(pr, old, me):
        found.append(_event(pr, "review_received", f"{review.author} {REVIEW_TEXT[review.state]}"))
    for comment in _new_events(pr, old, "IssueComment"):
        if who := _human(comment.actor, me):
            found.append(_event(pr, "conversation_comment", f"{who} commented"))
    if pr.in_merge_queue and not old.in_merge_queue:
        queued = _new_events(pr, old, "AddedToMergeQueueEvent")
        if who := _human(queued[-1].actor if queued else None, me):
            found.append(_event(pr, "merge_queue_added", f"{who} added it to the merge queue"))
    if old.in_merge_queue and not pr.in_merge_queue:
        removed = _new_events(pr, old, "RemovedFromMergeQueueEvent")
        reason = f": {removed[-1].reason}" if removed and removed[-1].reason else ""
        found.append(_event(pr, "merge_queue_removed", f"left the merge queue{reason}"))
    return found


def _reviewed(pr: PullRequest, old: PullRequest, me: str) -> list[Event]:
    """What changed on someone else's PR that I reviewed."""
    found: list[Event] = []
    if pr.head_oid != old.head_oid:
        found.append(_event(pr, "new_push", "new commits were pushed"))
    for comment in _new_events(pr, old, "IssueComment"):
        if pr.author and comment.actor == pr.author and _human(comment.actor, me):
            found.append(_event(pr, "author_replied", f"{pr.author} commented"))
    return found


def _open(snapshot: GitHubSnapshot) -> dict[str, PullRequest]:
    """The open PRs of every list, by URL."""
    found: dict[str, PullRequest] = {}
    for prs in (
        snapshot.mine,
        snapshot.reviewed_by_me,
        snapshot.direct_requests,
        snapshot.team_requests,
    ):
        for pr in prs:
            found.setdefault(pr.url, pr)
    return found


def changes(before: GitHubSnapshot, after: GitHubSnapshot) -> list[Event]:
    """The events between two snapshots."""
    me = after.login
    found: list[Event] = []
    old_mine = {pr.url: pr for pr in before.mine}
    now_mine = {pr.url for pr in after.mine}
    for pr in after.mine:
        if old := old_mine.get(pr.url):
            found += _mine(pr, old, me)
    merged = {pr.url: pr for pr in after.merged_recent}
    closed = {pr.url: pr for pr in after.closed_recent}
    for url in old_mine:
        if url in now_mine:
            continue
        if url in merged:
            found.append(_event(merged[url], "pr_merged", "was merged"))
        elif url in closed:
            found.append(_event(closed[url], "pr_closed", "was closed without merging"))
    old_open = _open(before)
    for pr in after.reviewed_by_me:
        if old := old_open.get(pr.url):
            found += _reviewed(pr, old, me)
    for pr in (*after.mine, *after.reviewed_by_me):
        if (old := old_open.get(pr.url)) and (replies := _replies(pr, old)):
            found.append(
                _event(
                    pr, "thread_reply", f"{replies} new thread repl{'y' if replies == 1 else 'ies'}"
                )
            )
    asked = {pr.url for pr in (*before.direct_requests, *before.team_requests)}
    for pr in after.direct_requests:
        if pr.url not in asked and not pr.is_draft:
            found.append(_event(pr, "review_requested_me", "review requested of you"))
    for pr in after.team_requests:
        if pr.url not in asked and not pr.is_draft:
            found.append(_event(pr, "review_requested_team", "review requested of your team"))
    return found
