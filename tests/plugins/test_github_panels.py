"""The GitHub plugin's panels, built as core builds the page."""

from datetime import timedelta

from conftest import (
    ME,
    NOW,
    approved_review,
    dashboard,
    entry,
    event,
    iced,
    local_snapshot,
    panel,
    pr,
    snapshot,
    thread,
    titles,
    todo,
)

from move_forward.plugins.github.models import (
    Check,
    DerivedSnapshot,
    ReviewerSuggestion,
    TeamPrScore,
)
from move_forward.plugins.notes.models import Ice

build = dashboard


# Reviews for me


def test_reviews_for_me_includes_direct_requests_and_changed_prs() -> None:
    reviewed_at = NOW - timedelta(days=1)
    pushed_after = pr(
        2,
        author="alice",
        reviews=[approved_review(ME, reviewed_at)],
        last_commit_at=NOW - timedelta(hours=2),
    )
    unchanged = pr(3, author="alice", reviews=[approved_review(ME, reviewed_at)])
    re_requested = pr(
        4, author="alice", reviews=[approved_review(ME, reviewed_at)], requested_users=[ME]
    )
    dashboard = build(
        github=snapshot(
            direct_requests=[pr(1, author="bob")],
            reviewed_by_me=[pushed_after, unchanged, re_requested],
        )
    )
    assert sorted(titles(dashboard, "github.reviews_for_me")) == ["PR 1", "PR 2", "PR 4"]
    details = {i.title: i.details for i in panel(dashboard, "github.reviews_for_me").items}
    assert details["PR 4"] == ["review re-requested"]
    assert details["PR 2"][0].startswith("pushed")


def test_drafts_sort_last_in_every_panel() -> None:
    newest_draft = pr(1, author="alice", is_draft=True, updated_at=NOW)
    older = pr(2, author="alice", updated_at=NOW - timedelta(days=3))
    dashboard = build(github=snapshot(direct_requests=[newest_draft, older]))
    assert titles(dashboard, "github.reviews_for_me") == ["PR 2", "PR 1"]
    assert [i.draft for i in panel(dashboard, "github.reviews_for_me").items] == [False, True]


# Needs a reviewer


def test_needs_reviewer_lists_team_only_and_unrequested_prs_with_suggestions() -> None:
    team_only = pr(1, requested_teams=["core"])
    nobody = pr(2)
    has_person = pr(3, requested_users=["alice"])
    approved = pr(4, requested_teams=["core"], reviews=[approved_review()])
    draft = pr(5, is_draft=True)
    dashboard = build(
        github=snapshot(mine=[team_only, nobody, has_person, approved, draft]),
        derived=DerivedSnapshot(
            suggestions={team_only.url: [ReviewerSuggestion(login="carol", commits=4)]}
        ),
    )
    items = {i.title: i for i in panel(dashboard, "github.needs_reviewer").items}
    assert set(items) == {"PR 1", "PR 2"}
    assert items["PR 1"].details == [
        "requested from team: core",
        "recent committers here: carol (4)",
    ]
    assert items["PR 2"].details == ["no reviewers requested"]


# Feedback


def test_feedback_collects_threads_changes_requested_and_unanswered_comments() -> None:
    pushed = NOW - timedelta(days=1)
    with_thread = pr(1, threads=[thread(), thread(resolved=True)])
    changes = pr(
        2,
        review_decision="CHANGES_REQUESTED",
        last_commit_at=pushed,
        events=[
            event("IssueComment", pushed - timedelta(hours=1), actor="alice"),  # before push
            event("IssueComment", pushed + timedelta(hours=1), actor="alice"),
            event("IssueComment", pushed + timedelta(hours=2), actor="some-bot"),
        ],
    )
    answered = pr(
        3,
        last_commit_at=pushed,
        events=[
            event("PullRequestReview", pushed + timedelta(hours=1), actor="alice"),
            event("IssueComment", pushed + timedelta(hours=2), actor=ME),
        ],
    )
    clean = pr(4)
    dashboard = build(github=snapshot(mine=[with_thread, changes, answered, clean]))
    items = {i.title: i for i in panel(dashboard, "github.feedback").items}
    assert set(items) == {"PR 1", "PR 2"}
    assert items["PR 1"].details == ["alice on src/x.py: Please rename this"]
    assert [link.text for link in items["PR 1"].links] == ["thread 1"]
    assert items["PR 2"].details == ["changes requested", "issuecomment by alice"]


def test_feedback_marks_outdated_threads() -> None:
    dashboard = build(github=snapshot(mine=[pr(1, threads=[thread(outdated=True)])]))
    assert panel(dashboard, "github.feedback").items[0].details[0].startswith("(outdated) ")


# CI, conflicts, ready


def test_ci_failures_show_failing_checks_and_recent_queue_removals() -> None:
    failing = pr(
        1,
        checks=[
            Check(name="tests", state="FAILURE", url="https://ci/1"),
            Check(name="lint", state="PENDING"),
        ],
    )
    evicted = pr(
        2,
        events=[event("RemovedFromMergeQueueEvent", NOW - timedelta(hours=3), reason="CI failed")],
    )
    old_eviction = pr(3, events=[event("RemovedFromMergeQueueEvent", NOW - timedelta(days=5))])
    draft = pr(4, is_draft=True, checks=[Check(name="tests", state="FAILURE")])
    review_only = pr(5, checks=[Check(name="review-policy/approvals", state="FAILURE")])
    dashboard = build(
        github=snapshot(mine=[failing, evicted, old_eviction, draft, review_only]),
        review_checks=["review-policy/approvals"],
    )
    items = {i.title: i for i in panel(dashboard, "github.ci_failures").items}
    assert set(items) == {"PR 1", "PR 2"}
    assert items["PR 1"].details == ["tests: FAILURE"]
    assert items["PR 1"].links[0].url == "https://ci/1"
    assert items["PR 2"].details == ["removed from merge queue 3h ago: CI failed"]


def test_conflicts() -> None:
    dashboard = build(
        github=snapshot(mine=[pr(1, mergeable="CONFLICTING"), pr(2, merge_state="DIRTY"), pr(3)])
    )
    assert sorted(titles(dashboard, "github.conflicts")) == ["PR 1", "PR 2"]


def test_ready_to_merge_requires_everything_and_respects_merge_conditions() -> None:
    ready = pr(1, reviews=[approved_review()], review_decision="APPROVED")
    held = pr(2, reviews=[approved_review()], review_decision="APPROVED")
    stacked = pr(3, reviews=[approved_review()], review_decision="APPROVED", base="me/APP-1-branch")
    threads = pr(4, reviews=[approved_review()], review_decision="APPROVED", threads=[thread()])
    pending = pr(
        5,
        reviews=[approved_review()],
        review_decision="APPROVED",
        checks=[Check(name="tests", state="PENDING")],
    )
    queued = pr(6, reviews=[approved_review()], review_decision="APPROVED", in_merge_queue=True)
    unapproved = pr(7)
    condition = entry(
        "APP-2 thing",
        [],
        **{"Merge conditions": ["don't merge until PR 1 is in"], "Where": [held.url]},
    )
    dashboard = build(
        github=snapshot(mine=[ready, held, stacked, threads, pending, queued, unapproved]),
        work_items=[condition],
    )
    assert titles(dashboard, "github.ready_to_merge") == ["PR 1"]
    assert titles(dashboard, "github.held") == ["PR 2"]
    assert panel(dashboard, "github.held").items[0].details == [
        "held: don't merge until PR 1 is in"
    ]


# Metadata


def test_metadata_flags_labels_links_and_ready_looking_drafts() -> None:
    unknown = pr(1, labels=["needs-team", "risk-low", "needs-risk"])
    no_link = pr(2, body="Just a description")
    empty = pr(3, body="  ", head="me/no-ticket")
    ready_draft = pr(4, is_draft=True)
    draft_with_todo = pr(5, is_draft=True)
    fine = pr(6)
    dashboard = build(
        github=snapshot(mine=[unknown, no_link, empty, ready_draft, draft_with_todo, fine]),
        work_items=[entry("APP-5 x", [todo("finish", "big")])],
        placeholder_labels=["needs-team", "needs-risk"],
        ticket_link_prefix="Link",
        jira_site="https://jira",  # so PR 5 and the entry are linked through APP-5
    )
    items = {i.title: i.details for i in panel(dashboard, "github.metadata").items}
    assert items == {
        "PR 1": ["needs-team", "needs-risk"],
        "PR 2": ["no Link line for APP-2"],
        "PR 3": ["empty description"],
        "PR 4": ["draft with green CI and no open todos: ready?"],
    }


# Team PRs


def test_team_prs_rank_by_codeowners_and_skip_direct_requests() -> None:
    direct = pr(1, author="alice")
    owned = pr(2, author="alice", files=["a", "b"])
    unowned = pr(3, author="alice")
    already = pr(4, author="alice", reviews=[approved_review("bob")])
    dashboard = build(
        github=snapshot(direct_requests=[direct], team_requests=[direct, unowned, owned, already]),
        derived=DerivedSnapshot(
            team_scores={
                owned.url: TeamPrScore(owned_files=2, required_files=1, my_files=1),
                unowned.url: TeamPrScore(owned_files=0, required_files=0, my_files=0),
            }
        ),
    )
    assert titles(dashboard, "github.team_prs") == ["PR 2", "PR 3", "PR 4"]
    assert panel(dashboard, "github.team_prs").items[0].details == [
        "2 files owned by my teams (1 required), 1 in dirs I work in"
    ]
    assert "waiting on core" in panel(dashboard, "github.team_prs").description


def test_iced_prs_are_left_out_of_my_pr_panels() -> None:
    parked = pr(1, requested_users=["alice"], mergeable="CONFLICTING", threads=[thread()])
    dashboard = build(
        github=snapshot(mine=[parked]),
        iced_stacks=[iced(parked.head, "x")],
    )
    for key in ("conflicts", "feedback", "needs_reviewer", "metadata"):
        key = f"github.{key}"
        assert titles(dashboard, key) == []
    assert titles(dashboard, "notes.iced") == [parked.head]
    # Reopen is notes'; git adds Delete branches for branches it still has, and Restore
    # branches for the ones it doesn't.
    actions = panel(dashboard, "notes.iced").items[0].actions
    assert [a.label for a in actions] == ["Reopen", "Restore branches"]


def test_a_pr_is_hidden_only_by_icing_in_its_own_repos_checkout() -> None:
    mine = pr(1, mergeable="CONFLICTING")

    def iced_in(repo_dir: str) -> Ice:
        return iced(mine.head, repo_dir)

    # git knows acme/widgets is checked out at /src/widgets.
    here = build(
        github=snapshot(mine=[mine]), local=local_snapshot(), iced_stacks=[iced_in("/src/widgets")]
    )
    assert titles(here, "github.conflicts") == []
    elsewhere = build(
        github=snapshot(mine=[mine]), local=local_snapshot(), iced_stacks=[iced_in("/src/other")]
    )
    assert titles(elsewhere, "github.conflicts") == [mine.title]


def test_act_now_ranks_my_prs_and_reviews_by_what_github_knows_of_them() -> None:
    small = pr(1, author="alice", size=40)
    big = pr(2, author="bob", size=900)
    ready = pr(3, reviews=[approved_review()], review_decision="APPROVED", merge_state="CLEAN")
    dashboard = build(
        github=snapshot(mine=[ready], direct_requests=[small, big]),
    )
    act = dashboard.act_now()
    assert [(i.title, i.details[0]) for i in act] == [
        (f"Merge: {ready.title}", "why: quick, closes acme/widgets#3"),
        (f"Review: {small.title}", "why: quick, alice waiting"),
        (f"Review: {big.title}", "why: bob waiting"),  # a day's review: no quick win
    ]


def test_act_now_says_how_long_a_review_or_a_reply_has_waited() -> None:
    asked = pr(
        1,
        author="alice",
        events=[
            event(
                "ReviewRequestedEvent", NOW - timedelta(days=5), reviewer_kind="user", reviewer=ME
            ),
            event(
                "ReviewRequestedEvent", NOW - timedelta(days=3), reviewer_kind="user", reviewer=ME
            ),
            event(
                "ReviewRequestedEvent", NOW - timedelta(hours=1), reviewer_kind="user", reviewer="x"
            ),
        ],
    )
    questioned = pr(
        2, threads=[thread("bob").model_copy(update={"created_at": NOW - timedelta(days=2)})]
    )
    act = build(github=snapshot(mine=[questioned], direct_requests=[asked])).act_now()
    why = {i.title: i.details[0] for i in act}
    assert why[f"Review: {asked.title}"] == "why: quick, alice waiting 3d"  # the latest request
    assert why[f"Reply: {questioned.title}"] == "why: quick, bob waiting 2d"
