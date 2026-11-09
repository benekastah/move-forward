"""What changed between two GitHub snapshots, as notification events.

A change of a PR's state is a change rule over the `pull_request`s github publishes (`RULES`,
`Plugin.change_rules`); what needs the snapshots (a new review, comment or timeline event, a
review requested) is `changes` (`Plugin.events`). Each kind says something once, when a
state changes; a PR the earlier snapshot didn't hold has no state to compare, so it only
ever says it's new (a review requested of me).
"""

from collections.abc import Callable, Collection

from ...api import ChangeRule, Event, Model, anchor
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


class _PrView(Model):
    """What the change rules read of a `pull_request`: the state fields are on my open PRs
    only."""

    title: str
    state: str | None
    repo: str
    number: int
    mine: bool
    draft: bool
    head_oid: str | None = None
    review_decision: str | None = None
    rollup_state: str | None = None
    mergeable: str | None = None
    in_merge_queue: bool | None = None
    failed_checks: list[str] | None = None


def _ready(pr: _PrView) -> bool:
    """Approved, green and conflict-free, and not yet queued: ready to merge."""
    return (
        not pr.draft
        and not pr.in_merge_queue
        and pr.review_decision == "APPROVED"
        and pr.rollup_state == "SUCCESS"
        and pr.mergeable == "MERGEABLE"
    )


def _failing(pr: _PrView) -> bool:
    return pr.rollup_state in FAILED


def _settled(old: _PrView, pr: _PrView) -> bool:
    """More than GitHub done recomputing mergeability, which it reports as UNKNOWN meanwhile:
    a return to its previous value is no news, but a review, check or commit changing is."""
    moved = (old.review_decision, old.rollup_state, old.head_oid)
    return old.mergeable != "UNKNOWN" or moved != (pr.review_decision, pr.rollup_state, pr.head_oid)


def _rule(
    kind: str,
    when: Callable[[_PrView, _PrView], bool],
    text: str | Callable[[_PrView], str],
    *,
    ends: bool = False,
) -> ChangeRule[_PrView]:
    """`kind` when `when` says one of my open PRs changed: one still open, or one that `ends`
    (merged or closed, by anyone)."""
    return ChangeRule(
        kind=kind,
        entity=PR,
        view=_PrView,
        when=lambda old, pr: (
            old.mine and old.state == "open" and (ends or pr.state == "open") and when(old, pr)
        ),
        text=text,
        title=lambda pr: f"{pr.repo}#{pr.number} {pr.title}",
    )


RULES = [
    _rule(
        "pr_mergeable", lambda o, p: _ready(p) and not _ready(o) and _settled(o, p), "can be merged"
    ),
    _rule(
        "ci_failed",
        lambda o, p: _failing(p) and not _failing(o),
        lambda p: f"CI failed: {', '.join(p.failed_checks)}" if p.failed_checks else "CI failed",
    ),
    _rule(
        "ci_recovered", lambda o, p: _failing(o) and p.rollup_state == "SUCCESS", "CI passes again"
    ),
    _rule(
        "merge_conflict",
        lambda o, p: (
            p.mergeable == "CONFLICTING"
            and (o.mergeable not in {"CONFLICTING", "UNKNOWN"} or p.head_oid != o.head_oid)
        ),
        "has a merge conflict",
    ),
    _rule("pr_merged", lambda o, p: p.state == "merged", "was merged", ends=True),
    _rule("pr_closed", lambda o, p: p.state == "closed", "was closed without merging", ends=True),
]
"""What's said of a PR's state changing (`Plugin.change_rules`)."""


def _event(pr: PullRequest, kind: str, text: str) -> Event:
    return Event(
        kind=kind,
        thing=pr.url,
        title=f"{pr.repo}#{pr.number} {pr.title}",
        text=text,
        anchor=anchor(PR, pr.url),
    )


def _other(login: str | None, me: str, ignored: Collection[str]) -> str | None:
    """`login` unless it's me or an ignored reviewer: bots' reviews and comments count."""
    return login if login and login != me and login not in ignored else None


def _human(login: str | None, me: str, ignored: Collection[str]) -> str | None:
    """`login` unless it's me, a bot or an ignored reviewer: for what only a person does."""
    return _other(login, me, ignored) if login and not login.endswith("[bot]") else None


def _new_events(pr: PullRequest, old: PullRequest, kind: str) -> list[TimelineEvent]:
    """The timeline events of `kind` that `pr` has and `old` hadn't."""
    seen = {(e.at, e.actor) for e in old.events if e.type == kind}
    return [e for e in pr.events if e.type == kind and (e.at, e.actor) not in seen]


def _new_reviews(
    pr: PullRequest, old: PullRequest, me: str, ignored: Collection[str]
) -> list[Review]:
    seen = {(r.author, r.state, r.submitted_at) for r in old.reviews}
    return [
        r
        for r in pr.reviews
        if (r.author, r.state, r.submitted_at) not in seen
        and r.state in REVIEW_TEXT
        and _other(r.author, me, ignored)
    ]


def _replies(pr: PullRequest, old: PullRequest) -> int:
    """How many comments the threads `old` knew have gained."""
    counts = {t.url: t.comment_count for t in old.threads if t.url}
    return sum(max(0, t.comment_count - counts[t.url]) for t in pr.threads if t.url in counts)


def _on_mine(pr: PullRequest, old: PullRequest, me: str, ignored: Collection[str]) -> list[Event]:
    """What changed on one of my PRs that's still open, beyond its state (`RULES`)."""
    found: list[Event] = []
    for review in _new_reviews(pr, old, me, ignored):
        found.append(_event(pr, "review_received", f"{review.author} {REVIEW_TEXT[review.state]}"))
    for comment in _new_events(pr, old, "IssueComment"):
        if who := _other(comment.actor, me, ignored):
            found.append(_event(pr, "conversation_comment", f"{who} commented"))
    if pr.in_merge_queue and not old.in_merge_queue:
        queued = _new_events(pr, old, "AddedToMergeQueueEvent")
        if who := _human(queued[-1].actor if queued else None, me, ignored):
            found.append(_event(pr, "merge_queue_added", f"{who} added it to the merge queue"))
    if old.in_merge_queue and not pr.in_merge_queue:
        removed = _new_events(pr, old, "RemovedFromMergeQueueEvent")
        reason = f": {removed[-1].reason}" if removed and removed[-1].reason else ""
        found.append(_event(pr, "merge_queue_removed", f"left the merge queue{reason}"))
    return found


def _reviewed(pr: PullRequest, old: PullRequest, me: str, ignored: Collection[str]) -> list[Event]:
    """What changed on someone else's PR that I reviewed."""
    found: list[Event] = []
    if pr.head_oid != old.head_oid:
        found.append(_event(pr, "new_push", "new commits were pushed"))
    for comment in _new_events(pr, old, "IssueComment"):
        if pr.author and comment.actor == pr.author and _other(comment.actor, me, ignored):
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


def changes(
    before: GitHubSnapshot, after: GitHubSnapshot, ignored: Collection[str] = ()
) -> list[Event]:
    """The events between two snapshots that no change rule says (`RULES`), leaving out
    what the `ignored` logins did."""
    me = after.login
    found: list[Event] = []
    old_mine = {pr.url: pr for pr in before.mine}
    for pr in after.mine:
        if old := old_mine.get(pr.url):
            found += _on_mine(pr, old, me, ignored)
    old_open = _open(before)
    for pr in after.reviewed_by_me:
        if old := old_open.get(pr.url):
            found += _reviewed(pr, old, me, ignored)
    for pr in (*after.mine, *after.reviewed_by_me):
        if (old := old_open.get(pr.url)) and (replies := _replies(pr, old)):
            found.append(
                _event(
                    pr, "thread_reply", f"{replies} new thread repl{'y' if replies == 1 else 'ies'}"
                )
            )
    # What was asked while a draft is news once it's ready, and asking me by name news after
    # asking my team; asking my team is none once I'm asked, before or now.
    asked_me = {pr.url for pr in before.direct_requests if not pr.is_draft}
    asked_team = {pr.url for pr in before.team_requests if not pr.is_draft}
    asked_me_now = {pr.url for pr in after.direct_requests}
    for pr in after.direct_requests:
        if pr.url not in asked_me and not pr.is_draft:
            found.append(_event(pr, "review_requested_me", "review requested of you"))
    for pr in after.team_requests:
        if pr.url not in asked_team | asked_me | asked_me_now and not pr.is_draft:
            found.append(_event(pr, "review_requested_team", "review requested of your team"))
    return found
