"""What GitHub notifications say, from two snapshots."""

from datetime import timedelta
from typing import ClassVar

import pytest
from conftest import ME, NOW, approved_review, event, pr, snapshot

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.notifications import NotificationRepository
from move_forward.page import anchor
from move_forward.plugin import Context
from move_forward.plugins.github.changes import KINDS, changes
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import Check, GitHubSnapshot, Review, Thread
from move_forward.plugins.github.plugin import GitHubPlugin

READY = {"review_decision": "APPROVED", "rollup_state": "SUCCESS", "mergeable": "MERGEABLE"}
WAITING = {"review_decision": "REVIEW_REQUIRED"}


def _kinds(before: GitHubSnapshot, after: GitHubSnapshot) -> list[tuple[str, str]]:
    return [(e.kind, e.text) for e in changes(before, after)]


def _mine(**fields: object) -> GitHubSnapshot:
    return snapshot(mine=[pr(1, **fields)])


def test_every_kind_the_plugin_offers_is_one_the_changes_use() -> None:
    assert GitHubPlugin.notification_kinds == KINDS


def test_a_pr_becoming_mergeable_is_said_once() -> None:
    assert _kinds(_mine(**WAITING), _mine(**READY)) == [("pr_mergeable", "can be merged")]
    assert _kinds(_mine(**READY), _mine(**READY)) == []


def test_a_pr_that_is_new_to_the_snapshot_says_nothing_of_its_state() -> None:
    assert _kinds(snapshot(), _mine(**READY)) == []


def test_a_draft_or_queued_pr_is_not_ready() -> None:
    assert _kinds(_mine(**WAITING), _mine(is_draft=True, **READY)) == []
    assert _kinds(_mine(**WAITING), _mine(in_merge_queue=True, **READY)) == []


def test_ci_failing_names_the_checks_and_recovering_is_said() -> None:
    red = {"rollup_state": "FAILURE", "checks": [Check(name="tests", state="FAILURE")]}
    assert _kinds(_mine(), _mine(**red)) == [("ci_failed", "CI failed: tests")]
    assert _kinds(_mine(**red), _mine(**red)) == []
    assert _kinds(_mine(**red), _mine()) == [("ci_recovered", "CI passes again")]


def test_a_merge_conflict_is_said_when_it_appears() -> None:
    assert _kinds(_mine(), _mine(mergeable="CONFLICTING")) == [
        ("merge_conflict", "has a merge conflict")
    ]


def test_new_reviews_by_others_are_said_but_not_mine_or_bots() -> None:
    reviews = [
        approved_review("alice"),
        Review(author=ME, state="COMMENTED", submitted_at=NOW),
        Review(author="dependabot[bot]", state="COMMENTED", submitted_at=NOW),
        Review(author="bob", state="CHANGES_REQUESTED", submitted_at=NOW),
    ]
    assert _kinds(_mine(), _mine(reviews=reviews)) == [
        ("review_received", "alice approved"),
        ("review_received", "bob requested changes"),
    ]
    assert _kinds(_mine(reviews=reviews), _mine(reviews=reviews)) == []


def test_new_comments_on_the_conversation_are_said() -> None:
    comment = event("IssueComment", NOW, actor="alice")
    mine = event("IssueComment", NOW + timedelta(minutes=1), actor=ME)
    assert _kinds(_mine(), _mine(events=[comment, mine])) == [
        ("conversation_comment", "alice commented")
    ]


def test_a_pr_merged_or_closed_is_said_whoever_did_it() -> None:
    assert _kinds(_mine(), snapshot(merged_recent=[pr(1, state="MERGED")])) == [
        ("pr_merged", "was merged")
    ]
    assert _kinds(_mine(), snapshot(closed_recent=[pr(1, state="CLOSED")])) == [
        ("pr_closed", "was closed without merging")
    ]
    assert _kinds(_mine(), snapshot()) == []  # gone from every search: nothing to say


def test_the_merge_queue_is_said_only_when_someone_else_queued_it() -> None:
    by_other = event("AddedToMergeQueueEvent", NOW, actor="alice")
    by_me = event("AddedToMergeQueueEvent", NOW, actor=ME)
    assert _kinds(_mine(), _mine(in_merge_queue=True, events=[by_other])) == [
        ("merge_queue_added", "alice added it to the merge queue")
    ]
    assert _kinds(_mine(), _mine(in_merge_queue=True, events=[by_me])) == []
    assert _kinds(_mine(), _mine(in_merge_queue=True)) == []  # who? unknown: not said


def test_leaving_the_merge_queue_says_why() -> None:
    out = event("RemovedFromMergeQueueEvent", NOW, reason="CI")
    assert _kinds(_mine(in_merge_queue=True), _mine(events=[out])) == [
        ("merge_queue_removed", "left the merge queue: CI")
    ]


def _thread(count: int) -> Thread:
    return Thread(resolved=False, outdated=False, url="https://x/t1", comment_count=count)


def test_a_reply_in_a_known_thread_is_said_but_a_new_thread_is_not_a_reply() -> None:
    assert _kinds(_mine(threads=[_thread(1)]), _mine(threads=[_thread(3)])) == [
        ("thread_reply", "2 new thread replies")
    ]
    assert _kinds(_mine(), _mine(threads=[_thread(1)])) == []


def test_a_review_request_is_said_when_new_or_asked_again() -> None:
    theirs = pr(7, author="alice", requested_users=[ME])
    assert _kinds(snapshot(), snapshot(direct_requests=[theirs])) == [
        ("review_requested_me", "review requested of you")
    ]
    assert _kinds(snapshot(direct_requests=[theirs]), snapshot(direct_requests=[theirs])) == []
    team = pr(8, author="alice")
    assert _kinds(snapshot(), snapshot(team_requests=[team])) == [
        ("review_requested_team", "review requested of your team")
    ]
    # reviewed before, then asked again
    assert _kinds(snapshot(reviewed_by_me=[theirs]), snapshot(direct_requests=[theirs])) == [
        ("review_requested_me", "review requested of you")
    ]


def test_new_commits_and_the_authors_comments_on_a_reviewed_pr_are_said() -> None:
    reviewed = pr(7, author="alice")
    pushed = pr(7, author="alice", head_oid="b" * 40)
    assert _kinds(snapshot(reviewed_by_me=[reviewed]), snapshot(reviewed_by_me=[pushed])) == [
        ("new_push", "new commits were pushed")
    ]
    by_author = event("IssueComment", NOW, actor="alice")
    by_other = event("IssueComment", NOW, actor="carol")
    replied = pr(7, author="alice", events=[by_author, by_other])
    assert _kinds(snapshot(reviewed_by_me=[reviewed]), snapshot(reviewed_by_me=[replied])) == [
        ("author_replied", "alice commented")
    ]


class _Scripted(GitHubPlugin):
    """GitHub that fetches the next of `snapshots` instead of asking GitHub."""

    snapshots: ClassVar[list[GitHubSnapshot]] = []

    def collect(self, ctx: Context[GitHubConfig]) -> GitHubSnapshot:
        return self.snapshots.pop(0)

    def after_collect(self, ctx: Context[GitHubConfig], graph: Graph) -> None:
        pass


def test_a_refresh_stores_the_events_of_subscribed_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        registry, "PLUGINS", [_Scripted() if p.name == "github" else p for p in registry.PLUGINS]
    )
    monkeypatch.setattr(_Scripted, "snapshots", [_mine(**WAITING), _mine(**READY)])
    config = Config.model_validate(
        {
            "email": "me@x",
            "github": {"org": "acme"},
            "notifications": {"subscribe": ["github.pr_mergeable", "github.ci_failed"]},
        }
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    refresh.refresh(config, db, NOW)
    [told] = NotificationRepository(db).take(NOW, timedelta(hours=1))
    assert (told.body, told.tag) == ("can be merged", pr(1).url)
    assert told.url == f"/#{anchor('pr', pr(1).url)}"
