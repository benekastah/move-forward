"""What a PR's fields say about it: its review state, checks, tickets and feedback."""

import re
from datetime import datetime

from ... import localtime
from .models import Check, PullRequest, Thread

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
BOT_LOGIN_RE = re.compile(r"bot\b|\[bot\]$|^copilot", re.IGNORECASE)
FAILING_CHECK_STATES = frozenset(
    {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
)
PASSING_CHECK_STATES = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})


def is_bot(login: str | None) -> bool:
    return login is None or bool(BOT_LOGIN_RE.search(login))


def approved(pr: PullRequest) -> bool:
    return pr.review_decision == "APPROVED" or any(r.state == "APPROVED" for r in pr.reviews)


def changes_requested(pr: PullRequest) -> bool:
    return pr.review_decision == "CHANGES_REQUESTED" or any(
        r.state == "CHANGES_REQUESTED" for r in pr.reviews
    )


def unresolved_threads(pr: PullRequest) -> list[Thread]:
    return [t for t in pr.threads if not t.resolved]


def failing_checks(pr: PullRequest) -> list[Check]:
    return [c for c in pr.checks if c.state in FAILING_CHECK_STATES]


def pending_checks(pr: PullRequest) -> list[Check]:
    return [c for c in pr.checks if c.state not in FAILING_CHECK_STATES | PASSING_CHECK_STATES]


def has_conflicts(pr: PullRequest) -> bool:
    return pr.mergeable == "CONFLICTING" or pr.merge_state == "DIRTY"


def has_ticket_link_line(body: str, prefix: str) -> bool:
    """Whether a line of the PR body starts with `prefix` and names a ticket key."""
    pattern = rf"^\s*{re.escape(prefix)}\b.*?[A-Z][A-Z0-9]+-\d+"
    return re.search(pattern, body, re.IGNORECASE | re.MULTILINE) is not None


def ticket_keys(pr: PullRequest) -> list[str]:
    """Jira keys in the branch name, title or body, most specific source first."""
    seen: list[str] = []
    for text in (pr.head, pr.title, pr.body):
        for key in JIRA_KEY_RE.findall(text):
            if key not in seen:
                seen.append(key)
    return seen


def unanswered_feedback(pr: PullRequest, me: str) -> list[str]:
    """Review and comment events by others after my last push that I haven't followed up on."""
    since = pr.last_commit_at
    pending: list[str] = []
    for event in sorted(pr.events, key=lambda e: e.at):
        if event.type not in ("PullRequestReview", "IssueComment"):
            continue
        if since and event.at <= since:
            continue
        if event.actor == me:
            pending.clear()
        elif not is_bot(event.actor):
            pending.append(f"{event.type.removeprefix('PullRequest').lower()} by {event.actor}")
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


def last_review_activity(pr: PullRequest) -> datetime | None:
    """The latest review request, review or non-bot comment."""
    times = [
        e.at
        for e in pr.events
        if e.type == "ReviewRequestedEvent"
        or (e.type in ("PullRequestReview", "IssueComment") and not is_bot(e.actor))
    ]
    times += [r.submitted_at for r in pr.reviews if r.submitted_at]
    return max(times) if times else None


def review_blockers(pr: PullRequest, me: str, review_checks: list[str]) -> list[str]:
    """Why the skill wouldn't nudge reviewers of my PR yet; empty when it's in order."""
    blockers: list[str] = []
    if approved(pr):
        blockers.append("already approved")
    if not pr.requested_users and not pr.requested_teams:
        blockers.append("no reviewers requested")
    if has_conflicts(pr):
        blockers.append("conflicts")
    if any(c.name not in review_checks for c in failing_checks(pr)):
        blockers.append("failing checks")
    if unresolved_threads(pr):
        blockers.append("unresolved threads")
    if unanswered_feedback(pr, me):
        blockers.append("unanswered feedback")
    return blockers
