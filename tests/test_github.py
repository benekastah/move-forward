import json
from datetime import UTC, datetime

import pytest

from move_forward_dashboard.plugins.github import client as github

RAW_PR: dict[str, object] = {
    "number": 7,
    "title": "Add thing",
    "url": "https://github.com/acme/widgets/pull/7",
    "isDraft": False,
    "state": "OPEN",
    "createdAt": "2026-10-28T10:00:00Z",
    "updatedAt": "2026-10-29T10:00:00Z",
    "mergedAt": None,
    "body": "Link APP-1",
    "headRefName": "me/APP-1",
    "headRefOid": "abc",
    "baseRefName": "main",
    "additions": 5,
    "deletions": 3,
    "changedFiles": 2,
    "isInMergeQueue": False,
    "mergeable": "MERGEABLE",
    "mergeStateStatus": "BLOCKED",
    "reviewDecision": None,
    "author": {"login": "me"},
    "repository": {"nameWithOwner": "acme/widgets", "defaultBranchRef": {"name": "main"}},
    "labels": {"nodes": [{"name": "needs-team"}]},
    "reviewRequests": {
        "nodes": [
            {"requestedReviewer": {"__typename": "User", "login": "alice"}},
            {"requestedReviewer": {"__typename": "Team", "slug": "core"}},
            {"requestedReviewer": {"__typename": "Mannequin"}},
            {"requestedReviewer": None},
        ]
    },
    "latestReviews": {
        "nodes": [
            {"author": {"login": "bob"}, "state": "APPROVED", "submittedAt": "2026-10-29T09:00:00Z"}
        ]
    },
    "reviewThreads": {
        "nodes": [
            {
                "isResolved": False,
                "isOutdated": True,
                "path": "a.py",
                "comments": {
                    "nodes": [
                        {
                            "author": {"login": "bob"},
                            "body": "fix",
                            "url": "https://github.com/acme/widgets/pull/7#r1",
                            "createdAt": "2026-10-29T08:00:00Z",
                        }
                    ]
                },
            }
        ]
    },
    "commits": {
        "nodes": [
            {
                "commit": {
                    "committedDate": "2026-10-28T09:00:00Z",
                    "statusCheckRollup": {
                        "state": "FAILURE",
                        "contexts": {
                            "nodes": [
                                {
                                    "__typename": "CheckRun",
                                    "name": "tests",
                                    "conclusion": "FAILURE",
                                    "status": "COMPLETED",
                                    "detailsUrl": "https://ci/1",
                                },
                                {
                                    "__typename": "CheckRun",
                                    "name": "lint",
                                    "conclusion": None,
                                    "status": "IN_PROGRESS",
                                    "detailsUrl": None,
                                },
                                {
                                    "__typename": "StatusContext",
                                    "context": "policy",
                                    "state": "SUCCESS",
                                    "targetUrl": "https://policy",
                                },
                            ]
                        },
                    },
                }
            }
        ]
    },
    "timelineItems": {
        "nodes": [
            {
                "__typename": "ReviewRequestedEvent",
                "createdAt": "2026-10-28T11:00:00Z",
                "requestedReviewer": {"__typename": "User", "login": "alice"},
            },
            {
                "__typename": "RemovedFromMergeQueueEvent",
                "createdAt": "2026-10-29T01:00:00Z",
                "reason": "CI",
            },
            {
                "__typename": "IssueComment",
                "createdAt": "2026-10-29T02:00:00Z",
                "author": {"login": "bob"},
            },
            {"__typename": "HeadRefForcePushedEvent", "createdAt": "2026-10-29T03:00:00Z"},
        ]
    },
    "files": {"nodes": [{"path": "a.py"}, {"path": "b/c.py"}]},
    "someFieldWeNeverAskedFor": {"nested": True},
}


def test_to_pull_request_flattens_everything() -> None:
    pr = github.to_pull_request(github.RawPullRequest.model_validate(RAW_PR))
    assert pr.repo == "acme/widgets"
    assert pr.default_branch == "main"
    assert pr.size == 8
    assert pr.labels == ["needs-team"]
    assert pr.requested_users == ["alice"]
    assert pr.requested_teams == ["core"]
    assert [(r.author, r.state) for r in pr.reviews] == [("bob", "APPROVED")]
    assert pr.threads[0].outdated and pr.threads[0].author == "bob"
    assert [(c.name, c.state) for c in pr.checks] == [
        ("tests", "FAILURE"),
        ("lint", "PENDING"),
        ("policy", "SUCCESS"),
    ]
    assert pr.last_commit_at == datetime(2026, 10, 28, 9, tzinfo=UTC)
    assert [e.type for e in pr.events] == [
        "ReviewRequestedEvent",
        "RemovedFromMergeQueueEvent",
        "IssueComment",
        "HeadRefForcePushedEvent",
    ]
    assert pr.events[0].reviewer == "alice" and pr.events[0].reviewer_kind == "user"
    assert pr.events[1].reason == "CI"
    assert pr.events[2].actor == "bob"
    assert pr.files == ["a.py", "b/c.py"]


def test_missing_optional_parts_are_tolerated() -> None:
    raw = dict(RAW_PR)
    raw["commits"] = {"nodes": []}
    raw["files"] = None
    raw["author"] = None
    del raw["labels"]
    pr = github.to_pull_request(github.RawPullRequest.model_validate(raw))
    assert pr.checks == [] and pr.last_commit_at is None and pr.rollup_state is None
    assert pr.files == [] and pr.author is None and pr.labels == []


def _response(nodes: list[object], has_next: bool, cursor: str | None) -> str:
    return json.dumps(
        {
            "data": {
                "search": {
                    "issueCount": 2,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                }
            }
        }
    )


def test_search_retries_a_failed_page_once(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def flaky_run(args: list[str], **_: object) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise github.CommandError("gh: unexpected end of JSON input")
        return _response([RAW_PR], False, None)

    monkeypatch.setattr(github, "run", flaky_run)
    assert [p.number for p in github.search_prs("q")] == [7]
    assert len(attempts) == 2

    attempts.clear()
    monkeypatch.setattr(github, "stopping", lambda: True)
    with pytest.raises(github.CommandError):
        github.search_prs("q")
    assert len(attempts) == 1  # no retry while shutting down


def test_search_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    pages = [
        _response([RAW_PR, None], True, "CURSOR"),
        _response([{**RAW_PR, "number": 8}], False, None),
    ]

    def fake_run(args: list[str], **_: object) -> str:
        calls.append(args)
        return pages.pop(0)

    monkeypatch.setattr(github, "run", fake_run)
    prs = github.search_prs("is:pr author:@me", with_files=True)
    assert [p.number for p in prs] == [7, 8]
    assert "after=CURSOR" not in calls[0] and "after=CURSOR" in calls[1]
    assert "withFiles=true" in calls[0]
