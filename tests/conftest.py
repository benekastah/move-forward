"""Shared factories: small, fully populated domain objects that tests tweak."""

from datetime import UTC, datetime, timedelta

from move_forward_dashboard.domain import (
    Check,
    GitHubSnapshot,
    PullRequest,
    Review,
    Thread,
    TimelineEvent,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
ME = "me"
REPO = "acme/widgets"


def pr(number: int = 1, **overrides: object) -> PullRequest:
    """An open, non-draft, green, unreviewed PR of mine on main, two days old."""
    created = NOW - timedelta(days=2)
    fields: dict[str, object] = {
        "url": f"https://github.com/{REPO}/pull/{number}",
        "number": number,
        "title": f"PR {number}",
        "repo": REPO,
        "default_branch": "main",
        "is_draft": False,
        "state": "OPEN",
        "created_at": created,
        "updated_at": created,
        "head": f"me/APP-{number}-branch",
        "head_oid": "a" * 40,
        "base": "main",
        "author": ME,
        "size": 10,
        "changed_files": 1,
        "mergeable": "MERGEABLE",
        "merge_state": "BLOCKED",
        "review_decision": "REVIEW_REQUIRED",
        "checks": [Check(name="tests", state="SUCCESS", url="https://ci/1")],
        "rollup_state": "SUCCESS",
        "last_commit_at": created,
        "body": f"Link https://jira/browse/APP-{number}",
    }
    fields.update(overrides)
    return PullRequest.model_validate(fields)


def approved_review(author: str = "alice", when: datetime = NOW) -> Review:
    return Review(author=author, state="APPROVED", submitted_at=when)


def thread(author: str = "alice", resolved: bool = False, outdated: bool = False) -> Thread:
    return Thread(
        resolved=resolved,
        outdated=outdated,
        path="src/x.py",
        author=author,
        body="Please rename this",
        url="https://github.com/acme/widgets/pull/1#discussion_r1",
        created_at=NOW - timedelta(hours=1),
    )


def event(type: str, at: datetime, **fields: object) -> TimelineEvent:
    return TimelineEvent.model_validate({"type": type, "at": at, **fields})


def snapshot(**overrides: object) -> GitHubSnapshot:
    fields: dict[str, object] = {
        "login": ME,
        "mine": [],
        "direct_requests": [],
        "reviewed_by_me": [],
        "team_requests": [],
        "merged_recent": [],
        "team_members": [ME, "alice"],
    }
    fields.update(overrides)
    return GitHubSnapshot.model_validate(fields)
