"""What a PR's fields say about it: its review state, checks, tickets and feedback."""

import re
from collections.abc import Collection
from datetime import datetime

from ... import localtime
from .models import Check, PullRequest, Thread, TimelineEvent

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
BOT_LOGIN_RE = re.compile(r"bot\b|\[bot\]$|^copilot", re.IGNORECASE)
FAILING_CHECK_STATES = frozenset(
    {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
)
PASSING_CHECK_STATES = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


def is_bot(login: str | None, ignored: Collection[str] = ()) -> bool:
    """Whether `login` is a bot or one of the `ignored` logins, whose feedback doesn't count."""
    return login is None or login in ignored or bool(BOT_LOGIN_RE.search(login))


def review_required(pr: PullRequest) -> bool:
    """Whether the base branch requires a review: GitHub's `reviewDecision` is null when none
    is required (it is APPROVED, CHANGES_REQUESTED or REVIEW_REQUIRED otherwise)."""
    return pr.review_decision is not None


def approved(pr: PullRequest) -> bool:
    """GitHub's verdict when it gives one (its rules count who may approve, and dismissed
    or stale reviews); else, where no review is required, whether anyone's latest review
    approves."""
    if pr.review_decision is not None:
        return pr.review_decision == "APPROVED"
    return any(r.state == "APPROVED" for r in pr.reviews)


def changes_requested(pr: PullRequest) -> bool:
    return pr.review_decision == "CHANGES_REQUESTED" or any(
        r.state == "CHANGES_REQUESTED" for r in pr.reviews
    )


def unresolved_threads(pr: PullRequest, ignored: Collection[str] = ()) -> list[Thread]:
    """The open threads, except those started by an `ignored` login."""
    return [t for t in pr.threads if not t.resolved and t.author not in ignored]


def failing_checks(pr: PullRequest) -> list[Check]:
    return [c for c in pr.checks if c.state in FAILING_CHECK_STATES]


def pending_checks(pr: PullRequest) -> list[Check]:
    return [c for c in pr.checks if c.state not in FAILING_CHECK_STATES | PASSING_CHECK_STATES]


def has_conflicts(pr: PullRequest) -> bool:
    return pr.mergeable == "CONFLICTING" or pr.merge_state == "DIRTY"


def has_ticket_link_line(body: str, prefixes: list[str]) -> bool:
    """Whether a line of the PR body starts with one of `prefixes` and names a ticket key,
    bare or in a URL."""
    words = "|".join(re.escape(p) for p in prefixes)
    pattern = rf"^\s*(?:{words})\b.*?[A-Z][A-Z0-9]+-\d+"
    return re.search(pattern, body, re.IGNORECASE | re.MULTILINE) is not None


def ticket_keys(pr: PullRequest) -> list[str]:
    """Jira keys in the branch name, title or body, most specific source first."""
    seen: list[str] = []
    for text in (pr.head, pr.title, pr.body):
        for key in JIRA_KEY_RE.findall(text):
            if key not in seen:
                seen.append(key)
    return seen


def _feedback_verb(event: TimelineEvent) -> str:
    """How feedback reads in the list: by the review's state, or a comment."""
    if event.type == "IssueComment":
        return "comments by"
    if event.state == "APPROVED" and event.has_body:
        return "approved with comments by"
    if event.state == "CHANGES_REQUESTED":
        return "changes requested by"
    return "reviewed by"


def _bare_approval(event: TimelineEvent) -> bool:
    """An approval with nothing written: there's nothing to answer."""
    return event.type == "PullRequestReview" and event.state == "APPROVED" and not event.has_body


def unanswered_feedback(pr: PullRequest, me: str, ignored: Collection[str] = ()) -> list[str]:
    """Review and comment events by others after my last push that I haven't followed up on.

    Review tools' (bots') count, unless they are `ignored`."""
    since = pr.last_commit_at
    pending: list[str] = []
    for event in sorted(pr.events, key=lambda e: e.at):
        if event.type not in ("PullRequestReview", "IssueComment"):
            continue
        if since and event.at <= since:
            continue
        if event.actor == me:
            pending.clear()
        elif event.actor and event.actor not in ignored and not _bare_approval(event):
            pending.append(f"{_feedback_verb(event)} {event.actor}")
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
        return f"pushed {localtime.day(pr.last_commit_at)} after my review"
    return None


def last_review_activity(pr: PullRequest, ignored: Collection[str] = ()) -> datetime | None:
    """The latest review request, review or non-bot comment."""
    times = [
        e.at
        for e in pr.events
        if e.type == "ReviewRequestedEvent"
        or (e.type in ("PullRequestReview", "IssueComment") and not is_bot(e.actor, ignored))
    ]
    times += [r.submitted_at for r in pr.reviews if r.submitted_at]
    return max(times) if times else None


BEHIND_REASON = "behind base: update the branch"
BLOCKED_REASON = "blocked by branch protection"
STATE_REASONS = (BEHIND_REASON, BLOCKED_REASON)
"""What GitHub's `mergeStateStatus` says on its own (BEHIND, BLOCKED): the PR looks ready
otherwise. The other states: CLEAN, HAS_HOOKS and UNSTABLE (only non-required checks
failing) can merge, DIRTY is a conflict, DRAFT a draft, and UNKNOWN isn't computed yet."""


def merge_blockers(pr: PullRequest, ignored: Collection[str] = ()) -> list[str]:
    """Why a PR can't be merged yet; empty when it's ready (Ready to merge's rule, besides
    the work notes' merge conditions)."""
    reasons: list[str] = []
    if review_required(pr) and not approved(pr):
        reasons.append("not approved")
    if unresolved_threads(pr, ignored):
        reasons.append("unresolved threads")
    if failing_checks(pr):
        reasons.append("failing checks")
    if pending_checks(pr):
        reasons.append("checks pending")
    if has_conflicts(pr):
        reasons.append("conflicts")
    if pr.default_branch and pr.base != pr.default_branch:
        reasons.append(f"stacked on {pr.base}")
    if pr.merge_state == "BEHIND":
        reasons.append(BEHIND_REASON)
    elif pr.merge_state == "BLOCKED" and not reasons:
        reasons.append(BLOCKED_REASON)  # something GitHub requires that we can't see
    return reasons


def progress(pr: PullRequest, ignored: Collection[str] = ()) -> str:
    """Where an open PR is, in a word or two: draft, ready to merge, CI failing or in review."""
    if pr.is_draft:
        return "draft"
    if not merge_blockers(pr, ignored):
        return "ready to merge"
    return "CI failing" if failing_checks(pr) else "in review"


def review_blockers(
    pr: PullRequest, me: str, policy_checks: list[str], ignored: Collection[str] = ()
) -> list[str]:
    """Why the skill wouldn't nudge reviewers of my PR yet; empty when it's in order."""
    blockers: list[str] = []
    if approved(pr):
        blockers.append("already approved")
    if not pr.requested_users and not pr.requested_teams:
        blockers.append("no reviewers requested")
    if has_conflicts(pr):
        blockers.append("conflicts")
    if any(c.name not in policy_checks for c in failing_checks(pr)):
        blockers.append("failing checks")
    if unresolved_threads(pr, ignored):
        blockers.append("unresolved threads")
    if unanswered_feedback(pr, me, ignored):
        blockers.append("unanswered feedback")
    return blockers
