"""The GitHub plugin's panels, built as core builds the page."""

from datetime import datetime, timedelta

from conftest import (
    ME,
    NOW,
    approved_review,
    dashboard,
    entry,
    event,
    iced,
    issue,
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
from move_forward.plugins.github.prs import has_ticket_link_line, review_blockers
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.notes.models import Ice

build = dashboard


# Reviews for me


def test_review_stages_hold_real_blockers_but_not_pending_ci() -> None:
    pending = pr(
        1,
        requested_users=["alice"],
        checks=[Check(name="tests", state="PENDING")],
    )
    failing_ci = pr(2, requested_users=["alice"], checks=[Check(name="tests", state="FAILURE")])
    failing_policy = pr(3, requested_users=["alice"], checks=[Check(name="risk", state="FAILURE")])
    conflict = pr(4, requested_users=["alice"], mergeable="CONFLICTING")
    feedback = pr(5, requested_users=["alice"], threads=[thread()])
    approved_but_changes = pr(
        6,
        requested_users=["alice"],
        review_decision="APPROVED",
        reviews=[
            approved_review(),
            approved_review("bob").model_copy(update={"state": "CHANGES_REQUESTED"}),
        ],
    )
    draft = pr(7, is_draft=True, checks=[Check(name="tests", state="FAILURE")])
    built = build(
        github=snapshot(
            mine=[
                pending,
                failing_ci,
                failing_policy,
                conflict,
                feedback,
                approved_but_changes,
                draft,
            ]
        ),
        policy_checks=["risk"],
    )
    assert titles(built, "github.ready_for_review") == ["PR 1"]
    assert set(titles(built, "github.fix_before_review")) == {
        "PR 2",
        "PR 3",
        "PR 4",
        "PR 5",
        "PR 6",
    }
    details = {item.title: item.details for item in panel(built, "github.fix_before_review").items}
    assert "CI failing: tests" in details["PR 2"]
    assert "policy failing: risk" in details["PR 3"]
    assert "merge conflict" in details["PR 4"]
    assert "1 unresolved review thread(s)" in details["PR 5"]
    assert "changes requested" in details["PR 6"]


def test_changes_requested_moves_to_waiting_after_push_and_re_request() -> None:
    requested_at = NOW - timedelta(days=1)
    pushed_at = NOW - timedelta(hours=2)
    asked_again = NOW - timedelta(hours=1)
    events = [
        event("PullRequestReview", requested_at, actor="bob", state="CHANGES_REQUESTED"),
        event("HeadRefForcePushedEvent", pushed_at),
        event("ReviewRequestedEvent", asked_again, reviewer_kind="user", reviewer="bob"),
    ]
    review_policy = [
        Check(
            name="PR policy: guidelines",
            state="FAILURE",
            description="Review required. PR must have approval from primary reviewer.",
        ),
        Check(
            name="PR policy: CODEOWNERS",
            state="FAILURE",
            description="Pending required reviews: team-a.",
        ),
    ]
    waiting = pr(
        1,
        requested_users=["bob"],
        review_decision="CHANGES_REQUESTED",
        last_commit_at=pushed_at,
        events=events,
        checks=review_policy,
    )
    unrelated_failure = pr(
        2,
        requested_users=["bob"],
        review_decision="CHANGES_REQUESTED",
        last_commit_at=pushed_at,
        events=events,
        checks=[*review_policy, Check(name="risk", state="FAILURE", description="Add risk label")],
    )
    not_requested_again = pr(
        3,
        requested_users=["bob"],
        review_decision="CHANGES_REQUESTED",
        last_commit_at=pushed_at,
        events=events[:-1],
        checks=review_policy,
    )
    built = build(
        github=snapshot(mine=[waiting, unrelated_failure, not_requested_again]),
        policy_checks=["PR policy: guidelines", "PR policy: CODEOWNERS", "risk"],
    )
    assert titles(built, "github.ready_for_review") == ["PR 1"]
    assert set(titles(built, "github.fix_before_review")) == {"PR 2", "PR 3"}
    assert titles(built, "github.feedback") == ["PR 3"]
    details = {item.title: item.details for item in panel(built, "github.fix_before_review").items}
    assert details["PR 2"] == ["policy failing: risk"]
    assert "changes requested" in details["PR 3"]


def test_a_policy_check_waiting_for_reviews_doesnt_hold_a_first_review() -> None:
    waiting = Check(
        name="PR policy: guidelines",
        state="FAILURE",
        description="Review required. PR must have approval from primary reviewer.",
    )
    first = pr(1, requested_users=["bob"], review_decision="REVIEW_REQUIRED", checks=[waiting])
    built = build(github=snapshot(mine=[first]), policy_checks=["PR policy: guidelines"])
    assert titles(built, "github.ready_for_review") == ["PR 1"]
    assert titles(built, "github.fix_before_review") == []
    assert titles(built, "github.policy") == []  # nothing for me to do: no Meet PR policy
    assert "failing policy checks" not in review_blockers(first, ME, ["PR policy: guidelines"])


def test_each_individual_policy_team_needs_one_of_its_members_requested() -> None:
    teams = ["bacon", "eggs"]
    memberships = {"acme/bacon": ["alex", "shared"], "acme/eggs": ["shared", "erin"]}
    only_eggs = pr(1, requested_teams=teams, requested_users=["erin"])
    shared = pr(2, requested_teams=teams, requested_users=["SHARED"])
    team_only = pr(3, requested_teams=["eggs"])
    built = build(
        github=snapshot(mine=[only_eggs, shared, team_only], team_memberships=memberships),
        default_team_reviewer_assignment="team",
        team_reviewer_assignment={"bacon": "individual"},
    )
    assert titles(built, "github.fix_before_review") == ["PR 1"]
    assert set(titles(built, "github.ready_for_review")) == {"PR 2", "PR 3"}
    assert "request a member of bacon" in panel(built, "github.fix_before_review").items[0].details

    both_individual = build(
        github=snapshot(mine=[only_eggs, shared], team_memberships=memberships),
        default_team_reviewer_assignment="individual",
    )
    assert titles(both_individual, "github.fix_before_review") == ["PR 1"]
    assert titles(both_individual, "github.ready_for_review") == ["PR 2"]


def test_reviews_for_me_contains_direct_requests_and_stale_approvals_not_team_requests() -> None:
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
    team_only = pr(5, author="alice", requested_teams=["core"])
    dashboard = build(
        github=snapshot(
            direct_requests=[pr(1, author="bob"), re_requested],
            reviewed_by_me=[pushed_after, unchanged, re_requested],
            team_requests=[team_only],
        )
    )
    assert sorted(titles(dashboard, "github.reviews_for_me")) == ["PR 1", "PR 2", "PR 4"]
    details = {i.title: i.details for i in panel(dashboard, "github.reviews_for_me").items}
    assert details["PR 1"] == ["review requested"]
    assert details["PR 2"][0].startswith("pushed")
    assert details["PR 4"] == ["review requested"]
    assert titles(dashboard, "github.team_prs") == ["PR 5"]


def test_a_direct_request_i_approved_only_returns_when_review_is_required() -> None:
    review = approved_review(ME, NOW - timedelta(days=1))
    still_approved = pr(
        1,
        author="alice",
        reviews=[review],
        last_commit_at=NOW,
        review_decision="APPROVED",
    )
    needs_review = pr(
        2,
        author="alice",
        reviews=[review],
        last_commit_at=NOW,
        review_decision="REVIEW_REQUIRED",
    )
    other_changes_requested = pr(
        3,
        author="alice",
        reviews=[review],
        last_commit_at=NOW,
        review_decision="CHANGES_REQUESTED",
    )
    built = build(
        github=snapshot(direct_requests=[still_approved, needs_review, other_changes_requested])
    )
    assert titles(built, "github.reviews_for_me") == ["PR 2"]


def test_a_new_push_after_my_approval_needs_github_to_require_review() -> None:
    approved_at = NOW - timedelta(days=1)
    old_approval = [approved_review(ME, approved_at)]
    stale = pr(
        1,
        author="alice",
        reviews=old_approval,
        last_commit_at=NOW,
        review_decision="REVIEW_REQUIRED",
    )
    still_valid = pr(
        2,
        author="alice",
        reviews=old_approval,
        last_commit_at=NOW,
        review_decision="APPROVED",
    )
    no_new_push = pr(
        3,
        author="alice",
        reviews=old_approval,
        last_commit_at=approved_at - timedelta(hours=1),
        review_decision="REVIEW_REQUIRED",
    )
    built = build(github=snapshot(reviewed_by_me=[stale, still_valid, no_new_push]))
    assert titles(built, "github.reviews_for_me") == ["PR 1"]


def test_act_now_does_not_suggest_reviewing_a_draft() -> None:
    draft = pr(1, author="alice", is_draft=True)
    ready = pr(2, author="bob")
    dashboard = build(github=snapshot(direct_requests=[draft, ready]))
    assert [i.title for i in dashboard.act_now()] == [f"Review: {ready.title}"]
    assert len(panel(dashboard, "github.reviews_for_me").items) == 1


def test_review_effort_needs_both_the_lines_and_the_files_within_a_limit() -> None:
    def efforts(**settings: object) -> dict[str, str | None]:
        sizes = {
            "small": (100, 5),
            "many files": (50, 6),
            "medium": (300, 12),
            "many lines": (301, 3),
            "big": (100, 13),
        }
        prs = [
            pr(n, author="alice", size=lines, changed_files=files)
            for n, (lines, files) in enumerate(sizes.values(), 1)
        ]
        built = build(github=snapshot(direct_requests=prs), **settings)
        by_pr = {
            i.title: i.signals.effort
            for i in panel(built, "github.reviews_for_me").items
            if i.signals
        }
        return dict(zip(sizes, [by_pr[p.title] for p in prs], strict=True))

    assert efforts() == {
        "small": "minutes",
        "many files": "hour",
        "medium": "hour",
        "many lines": "day",
        "big": "day",
    }
    assert efforts(quick_review_max_files=6)["many files"] == "minutes"


def test_drafts_sort_last_in_every_panel() -> None:
    newest_draft = pr(1, author="alice", is_draft=True, updated_at=NOW)
    older = pr(2, author="alice", updated_at=NOW - timedelta(days=3))
    dashboard = build(github=snapshot(direct_requests=[newest_draft, older]))
    assert titles(dashboard, "github.reviews_for_me") == ["PR 2"]
    assert [i.deferred for i in panel(dashboard, "github.reviews_for_me").items] == [False]


# Needs a reviewer


def test_needs_reviewer_lists_team_only_and_unrequested_prs_with_suggestions() -> None:
    team_only = pr(1, requested_teams=["core"])
    nobody = pr(2)
    has_person = pr(3, requested_users=["alice"])
    approved = pr(
        4, requested_teams=["core"], reviews=[approved_review()], review_decision="APPROVED"
    )
    draft = pr(5, is_draft=True)
    dashboard = build(
        github=snapshot(mine=[team_only, nobody, has_person, approved, draft]),
        team_reviewer_assignment={"core": "individual"},
        derived=DerivedSnapshot(
            suggestions={team_only.url: [ReviewerSuggestion(login="carol", commits=4)]}
        ),
    )
    items = {i.title: i for i in panel(dashboard, "github.needs_reviewer").items}
    assert set(items) == {"PR 1", "PR 2", "PR 4"}
    assert items["PR 1"].details == [
        "request a member of core",
        "recent committers here: carol (4)",
    ]
    assert items["PR 2"].details == ["no reviewer requested"]


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
    assert items["PR 2"].details == [
        "changes requested",
        "comments by alice",
        "comments by some-bot",  # review tools are bots too: theirs is feedback
    ]


def test_feedback_words_a_review_by_its_state_and_skips_a_bare_approval() -> None:
    pushed = NOW - timedelta(days=1)

    def at(hours: int) -> datetime:
        return pushed + timedelta(hours=hours)

    reviewed = pr(
        1,
        last_commit_at=pushed,
        events=[
            event("PullRequestReview", at(1), actor="alice", state="APPROVED", has_body=True),
            event("PullRequestReview", at(1), actor="erin", state="APPROVED"),  # nothing to answer
            event("PullRequestReview", at(2), actor="bob", state="CHANGES_REQUESTED"),
            event("PullRequestReview", at(3), actor="carol", state="COMMENTED"),
            event("PullRequestReview", at(4), actor="dave"),  # stored before states were kept
        ],
    )
    dashboard = build(github=snapshot(mine=[reviewed]))
    [item] = panel(dashboard, "github.feedback").items
    assert item.details == [
        "approved with comments by alice",
        "changes requested by bob",
        "reviewed by carol",
        "reviewed by dave",
    ]


def test_feedback_leaves_out_comments_and_threads_of_ignored_reviewers() -> None:
    pushed = NOW - timedelta(days=1)
    noisy = pr(
        1,
        last_commit_at=pushed,
        threads=[thread(author="policy-bot")],
        events=[event("IssueComment", pushed + timedelta(hours=1), actor="policy-bot")],
    )
    dashboard = build(github=snapshot(mine=[noisy]), ignored_reviewers=["policy-bot"])
    assert panel(dashboard, "github.feedback").items == []
    assert panel(build(github=snapshot(mine=[noisy])), "github.feedback").items != []


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
        rollup_state="PENDING",
        events=[event("RemovedFromMergeQueueEvent", NOW - timedelta(hours=3), reason="CI failed")],
    )
    old_eviction = pr(3, events=[event("RemovedFromMergeQueueEvent", NOW - timedelta(days=5))])
    draft = pr(4, is_draft=True, checks=[Check(name="tests", state="FAILURE")])
    review_only = pr(5, checks=[Check(name="review-policy/approvals", state="FAILURE")])
    dashboard = build(
        github=snapshot(mine=[failing, evicted, old_eviction, draft, review_only]),
        policy_checks=["review-policy/approvals"],
    )
    items = {i.title: i for i in panel(dashboard, "github.ci_failures").items}
    assert set(items) == {"PR 1", "PR 2"}
    assert items["PR 1"].details == ["tests: FAILURE"]
    assert items["PR 1"].links[0].url == "https://ci/1"
    assert items["PR 2"].details == ["removed from merge queue 3h ago: CI failed"]


def test_failing_policy_checks_are_pr_policy_items_not_ci_failures() -> None:
    label = Check(name="pr-policy/risk-label", state="FAILURE", url="https://ci/policy")
    approved_pr = pr(1, reviews=[approved_review()], review_decision="APPROVED", checks=[label])
    draft = pr(2, is_draft=True, checks=[label])
    dashboard = build(
        github=snapshot(mine=[approved_pr, draft]), policy_checks=["pr-policy/risk-label"]
    )
    (item,) = panel(dashboard, "github.policy").items
    assert item.title == "PR 1"
    assert item.details == ["pr-policy/risk-label: FAILURE"]
    assert item.links[0].url == "https://ci/policy"
    assert titles(dashboard, "github.ci_failures") == []
    assert titles(dashboard, "github.ready_to_merge") == []  # held until the policy passes


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
        ticket_link_prefixes=["Link"],
        jira_site="https://jira",  # so PR 5 and the entry are linked through APP-5
    )
    items = {i.title: i.details for i in panel(dashboard, "github.metadata").items}
    assert items == {
        "PR 1": ["needs-team", "needs-risk"],
        "PR 2": ["no Link line for APP-2"],
        "PR 3": ["empty description"],
        "PR 4": ["draft with green CI and no open todos: ready?"],
        "PR 5": [],
    }


def test_housekeeping_groups_drafts_by_the_prs_their_base_branches_belong_to() -> None:
    root = pr(1, head="feature/root", is_draft=True)
    child = pr(2, head="feature/child", base="feature/root", is_draft=True)
    grandchild = pr(3, head="feature/grandchild", base="feature/child", is_draft=True)
    sibling = pr(4, head="feature/sibling", base="feature/root", is_draft=True)
    built = build(github=snapshot(mine=[grandchild, sibling, child, root]))
    items = {item.title: item for item in panel(built, "github.metadata").items}
    assert items["PR 2"].parent_anchor == items["PR 1"].anchor
    assert items["PR 3"].parent_anchor == items["PR 2"].anchor
    assert items["PR 4"].parent_anchor == items["PR 1"].anchor
    assert not items["PR 1"].children_open
    assert not items["PR 2"].children_open

    open_root = root.model_copy(update={"is_draft": False})
    built = build(github=snapshot(mine=[grandchild, sibling, child, open_root]))
    items = {item.title: item for item in panel(built, "github.metadata").items}
    assert items["PR 1"].children_open
    assert not items["PR 2"].children_open

    open_child = child.model_copy(update={"is_draft": False})
    built = build(github=snapshot(mine=[grandchild, sibling, open_child, root]))
    items = {item.title: item for item in panel(built, "github.metadata").items}
    assert items["PR 1"].children_open
    assert items["PR 2"].children_open


# Team PRs


def test_team_prs_rank_by_codeowners_and_skip_direct_requests() -> None:
    direct = pr(1, author="alice")
    owned = pr(2, author="alice", files=["a", "b"])
    unowned = pr(3, author="alice")
    already = pr(4, author="alice", reviews=[approved_review("bob")], review_decision="APPROVED")
    dashboard = build(
        github=snapshot(direct_requests=[direct], team_requests=[direct, unowned, owned, already]),
        derived=DerivedSnapshot(
            team_scores={
                owned.url: TeamPrScore(owned_files=2, required_files=1, my_files=1),
                unowned.url: TeamPrScore(owned_files=0, required_files=0, my_files=0),
            }
        ),
    )
    assert titles(dashboard, "github.team_prs") == ["PR 2", "PR 3"]  # PR 4 is approved
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
    assert [a.label for a in actions if a.group != "Importance"] == [
        "Reopen",
        "Note",
        "Restore branches",
    ]


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


def test_a_merge_condition_holds_only_its_own_pr() -> None:
    api = pr(1, reviews=[approved_review()], review_decision="APPROVED")
    web = pr(2, reviews=[approved_review()], review_decision="APPROVED")
    work = entry(
        "APP-1 both",
        [],
        **{
            "Where": [f"{api.url} {web.url}"],
            "Merge conditions": [f"{api.url}: after the migration"],
        },
    )
    dashboard = build(github=snapshot(mine=[api, web]), work_items=[work])
    assert titles(dashboard, "github.held") == [api.title]
    assert titles(dashboard, "github.ready_to_merge") == [web.title]


def test_the_merge_queue_panel_lists_my_queued_prs_and_is_not_for_act_now() -> None:
    queued = pr(1, in_merge_queue=True)
    dashboard = build(github=snapshot(mine=[queued, pr(2)]))
    queue = panel(dashboard, "github.merge_queue")
    assert [i.title for i in queue.items] == [queued.title]
    assert not queue.act_now
    assert all(i.title != queued.title or i.signals is None for i in queue.items)


def test_housekeeping_says_when_a_pr_may_need_or_is_worth_splitting() -> None:
    body = "text"
    maybe = pr(1, body=body, size=600)
    worth = pr(2, body=body, size=800)
    labeled = pr(3, body=body, size=10, labels=["size/XL"])
    fine = pr(4, body=body, size=599)
    built = build(github=snapshot(mine=[maybe, worth, labeled, fine]), split_pr_labels=["size/XL"])
    details = {i.title: i.details for i in panel(built, "github.metadata").items}
    assert details == {
        maybe.title: ["may need splitting: 600 lines"],
        worth.title: ["worth splitting: 800 lines"],
        labeled.title: ["worth splitting: labeled size/XL"],
    }


def test_a_review_is_sized_by_the_part_i_or_my_teams_own() -> None:
    big = pr(1, author="alice", size=900, changed_files=119)
    whole = pr(2, author="alice", size=900, changed_files=119)
    score = TeamPrScore(owned_files=2, required_files=0, my_files=0, part_files=2, part_lines=12)
    built = build(
        github=snapshot(direct_requests=[big, whole]),
        derived=DerivedSnapshot(team_scores={big.url: score}),
    )
    items = {i.title: i for i in panel(built, "github.reviews_for_me").items}
    big_signals = items[big.title].signals
    assert big_signals is not None
    assert big_signals.effort == "minutes"
    assert "my part: 12 lines in 2 of 119 files" in items[big.title].details
    assert items[big.title].badges == ["+900 lines", "119 files"]
    whole_signals = items[whole.title].signals
    assert whole_signals is not None
    assert whole_signals.effort == "day"
    assert items[whole.title].details == ["review requested"]


def test_a_ticket_link_line_starts_with_any_prefix_and_names_a_key_or_a_url() -> None:
    prefixes = ["Link", "Closes"]
    assert has_ticket_link_line("Closes RD-123", prefixes)
    assert has_ticket_link_line("text\nLink https://acme.atlassian.net/browse/RD-123", prefixes)
    assert not has_ticket_link_line("Fixes RD-123", prefixes)
    assert not has_ticket_link_line("Closes nothing", prefixes)
    assert not has_ticket_link_line("Linkage RD-123", prefixes)  # a word boundary


def test_threads_of_ignored_reviewers_dont_hold_a_merge_or_a_ready_draft() -> None:
    bot_thread = thread(author="policy-bot")
    ready = pr(1, reviews=[approved_review()], review_decision="APPROVED", threads=[bot_thread])
    draft = pr(2, is_draft=True, rollup_state="SUCCESS", body="x", threads=[bot_thread])
    snap = snapshot(mine=[ready, draft])
    held = build(github=snap)
    assert titles(held, "github.ready_to_merge") == []
    held_details = [d for i in panel(held, "github.metadata").items for d in i.details]
    assert "draft with green CI and no open todos: ready?" not in held_details
    ignoring = build(github=snap, ignored_reviewers=["policy-bot"])
    assert titles(ignoring, "github.ready_to_merge") == ["PR 1"]
    details = [d for i in panel(ignoring, "github.metadata").items for d in i.details]
    assert "draft with green CI and no open todos: ready?" in details


def test_an_eviction_stops_showing_once_the_pr_is_queued_again_or_green_and_the_window_is_a_setting() -> (
    None
):
    removed = event("RemovedFromMergeQueueEvent", NOW - timedelta(days=3), reason="CI failed")
    stale = pr(1, rollup_state="PENDING", events=[removed])
    back = pr(2, rollup_state="PENDING", in_merge_queue=True, events=[removed])
    queued_since = pr(
        3,
        rollup_state="PENDING",
        events=[removed, event("AddedToMergeQueueEvent", NOW - timedelta(days=2), actor="me")],
    )
    green = pr(4, rollup_state="SUCCESS", events=[removed])
    built = build(github=snapshot(mine=[stale, back, queued_since, green]))
    assert titles(built, "github.ci_failures") == []  # three days is past the window
    wider = build(
        github=snapshot(mine=[stale, back, queued_since, green]), merge_queue_eviction_days=4
    )
    assert titles(wider, "github.ci_failures") == ["PR 1"]


def test_a_pr_behind_its_base_or_blocked_is_held_with_the_reason_not_ready() -> None:
    approved = {"reviews": [approved_review()], "review_decision": "APPROVED"}
    behind = pr(1, merge_state="BEHIND", **approved)
    blocked = pr(2, merge_state="BLOCKED", **approved)
    unapproved_blocked = pr(3, merge_state="BLOCKED")
    clean = pr(4, merge_state="CLEAN", **approved)
    built = build(github=snapshot(mine=[behind, blocked, unapproved_blocked, clean]))
    assert titles(built, "github.ready_to_merge") == ["PR 4"]
    held = {i.title: i.details for i in panel(built, "github.held").items}
    assert held == {
        "PR 1": ["held: behind base: update the branch"],
        "PR 2": ["held: blocked by branch protection"],
    }


def test_approval_follows_githubs_decision_and_none_is_demanded_where_no_review_is_required() -> (
    None
):
    stale_approval = pr(1, reviews=[approved_review()], review_decision="REVIEW_REQUIRED")
    not_required = pr(2, review_decision=None, requested_users=[], requested_teams=[])
    built = build(github=snapshot(mine=[stale_approval, not_required]))
    assert titles(built, "github.ready_to_merge") == ["PR 2"]  # approval isn't required there
    assert titles(built, "github.needs_reviewer") == ["PR 1"]  # GitHub says it isn't approved


def test_feedback_is_judged_from_the_latest_push_and_a_thread_reply_clears_nothing() -> None:
    commit_date = NOW - timedelta(days=3)
    rebased = pr(
        1,
        last_commit_at=commit_date,  # an old commit, rebased and force-pushed yesterday
        events=[
            event("IssueComment", NOW - timedelta(days=2), actor="alice"),  # before the push
            event("HeadRefForcePushedEvent", NOW - timedelta(days=1)),
            event("IssueComment", NOW - timedelta(hours=5), actor="bob"),
        ],
    )
    answered_in_a_thread = pr(
        2,
        last_commit_at=commit_date,
        events=[
            event("IssueComment", NOW - timedelta(hours=5), actor="alice"),
            event("PullRequestReview", NOW - timedelta(hours=4), actor=ME),  # a thread reply
        ],
    )
    answered_in_the_conversation = pr(
        3,
        last_commit_at=commit_date,
        events=[
            event("IssueComment", NOW - timedelta(hours=5), actor="alice"),
            event("IssueComment", NOW - timedelta(hours=4), actor=ME),
        ],
    )
    built = build(
        github=snapshot(mine=[rebased, answered_in_a_thread, answered_in_the_conversation])
    )
    items = {i.title: i.details for i in panel(built, "github.feedback").items}
    assert items == {"PR 1": ["comments by bob"], "PR 2": ["comments by alice"]}


def test_a_direct_review_waits_since_the_request_and_a_stale_approval_since_the_push() -> None:
    reviewed_at = NOW - timedelta(days=3)
    pushed_at = NOW - timedelta(hours=2)
    requested_at = NOW - timedelta(hours=7)
    repushed = pr(
        1,
        author="alice",
        reviews=[approved_review(ME, reviewed_at)],
        last_commit_at=NOW - timedelta(days=4),  # an old commit, force-pushed two hours ago
        events=[event("HeadRefForcePushedEvent", pushed_at)],
    )
    direct = pr(
        2,
        author="bob",
        events=[event("ReviewRequestedEvent", requested_at, reviewer_kind="user", reviewer=ME)],
    )
    built = build(github=snapshot(direct_requests=[direct], reviewed_by_me=[repushed]))
    waiting = {
        i.title: i.signals.waiting_since
        for i in panel(built, "github.reviews_for_me").items
        if i.signals
    }
    assert waiting == {"PR 1": pushed_at, "PR 2": requested_at}


def test_ticket_keys_come_from_jiras_projects_else_a_guess_that_skips_utf_8_and_cve() -> None:
    plain = pr(1, title="Handle UTF-8 and CVE-2024-1234", body="x", head="me/plain")
    other = pr(2, title="Fix ZZ-5", body="x", head="me/other")
    ticketed = pr(3, title="Fix APP-7", body="x", head="me/ticketed")
    snap = snapshot(mine=[plain, other, ticketed])

    def flagged(**settings: object) -> list[str]:
        built = build(github=snap, ticket_link_prefixes=["Link"], **settings)
        return sorted(i.title for i in panel(built, "github.metadata").items)

    assert flagged() == ["Fix APP-7", "Fix ZZ-5"]  # a guess: ZZ-5 and APP-7 look like tickets
    known = JiraSnapshot(issues=[issue("APP-1")])  # Jira has seen project APP
    assert flagged(jira=known) == ["Fix APP-7"]  # ZZ isn't one of its projects


def test_conflicts_skip_drafts_and_a_stacked_pr_is_told_to_restack() -> None:
    base = pr(1, head="me/base", requested_users=["alice"])
    stacked = pr(2, head="me/top", base="me/base", mergeable="CONFLICTING")
    draft = pr(3, mergeable="CONFLICTING", is_draft=True)
    plain = pr(4, mergeable="CONFLICTING")
    built = build(github=snapshot(mine=[base, stacked, draft, plain]))
    items = {i.title: i for i in panel(built, "github.conflicts").items}
    assert set(items) == {"PR 2", "PR 4"}
    assert items["PR 2"].details[0] == "stacked on me/base: restack (git stack sync)"
    assert items["PR 2"].signals and items["PR 2"].signals.verb == "Restack the stack"
    assert items["PR 4"].signals is None  # Resolve conflicts, from the panel
    assert sorted(i.title for i in built.act_now()) == [
        "Resolve conflicts: PR 4",
        "Restack the stack: PR 2",
    ]


def test_housekeeping_flags_my_drafts_untouched_for_stale_draft_days() -> None:
    stale = pr(
        1, is_draft=True, body="x", updated_at=NOW - timedelta(days=15), rollup_state="FAILURE"
    )
    fresh = pr(
        2, is_draft=True, body="x", updated_at=NOW - timedelta(days=13), rollup_state="FAILURE"
    )
    old_open = pr(3, body="x", updated_at=NOW - timedelta(days=40))  # not a draft
    snap = snapshot(mine=[stale, fresh, old_open])
    details = {i.title: i.details for i in panel(build(github=snap), "github.metadata").items}
    assert details == {"PR 1": ["stale draft: untouched 15d"], "PR 2": []}
    off = build(github=snap, stale_draft_days=0)
    assert titles(off, "github.metadata") == ["PR 1", "PR 2"]


def test_a_pr_that_mentions_me_is_a_reply_unless_already_asked_for_or_a_draft() -> None:
    mention = pr(1, author="alice")
    asked = pr(2, author="bob")
    draft = pr(3, author="carol", is_draft=True)
    built = build(
        github=snapshot(direct_requests=[asked], mentioned=[mention, asked, draft]),
    )
    replies = {i.title: i for i in panel(built, "github.replies_for_me").items}
    assert replies["PR 1"].details == ["mentions you"]
    assert replies["PR 1"].signals and replies["PR 1"].signals.verb == "Reply"
    assert titles(built, "github.reviews_for_me") == ["PR 2"]
    assert "PR 3" not in replies
    assert "Reply: PR 1" in [i.title for i in built.act_now()]


def test_a_team_pr_says_my_part_and_the_required_files_on_one_line() -> None:
    mine = pr(1, author="alice", changed_files=23)
    score = TeamPrScore(owned_files=5, required_files=1, my_files=0, part_files=5, part_lines=51)
    built = build(
        github=snapshot(team_requests=[mine]),
        derived=DerivedSnapshot(team_scores={mine.url: score}),
    )
    assert panel(built, "github.team_prs").items[0].details == [
        "my part: 51 lines in 5 of 23 files (1 required)"
    ]
    worked = score.model_copy(update={"my_files": 3})
    built = build(
        github=snapshot(team_requests=[mine]),
        derived=DerivedSnapshot(team_scores={mine.url: worked}),
    )
    assert panel(built, "github.team_prs").items[0].details == [
        "my part: 51 lines in 5 of 23 files (1 required), 3 in dirs I work in"
    ]


def test_ticket_link_prefixes_in_the_message_are_joined_with_commas_and_or() -> None:
    def message(prefixes: list[str]) -> str:
        unlinked = pr(1, body="text", head="me/x", title="Fix APP-9")
        built = build(
            github=snapshot(mine=[unlinked]), ticket_link_prefixes=prefixes, jira_site="https://j"
        )
        return panel(built, "github.metadata").items[0].details[0]

    assert message(["Link"]) == "no Link line for APP-9"
    assert message(["Link", "Closes"]) == "no Link or Closes line for APP-9"
    assert message(["Link", "Links", "Closes"]) == "no Link, Links or Closes line for APP-9"


def test_a_review_whose_author_has_open_threads_is_not_listed_twice() -> None:
    pushed = NOW - timedelta(days=1)
    reviewed = pr(
        1,
        last_commit_at=pushed,
        threads=[thread(author="copilot-pull-request-reviewer")],
        events=[
            event(
                "PullRequestReview",
                pushed + timedelta(hours=1),
                actor="copilot-pull-request-reviewer",
                state="COMMENTED",
            ),
            event("PullRequestReview", pushed + timedelta(hours=2), actor="bob", state="COMMENTED"),
        ],
    )
    resolved = pr(
        2,
        last_commit_at=pushed,
        threads=[thread(author="bob", resolved=True)],
        events=[event("PullRequestReview", pushed + timedelta(hours=1), actor="bob")],
    )
    built = build(github=snapshot(mine=[reviewed, resolved]))
    items = {i.title: i.details for i in panel(built, "github.feedback").items}
    assert items["PR 1"] == [
        "copilot-pull-request-reviewer on src/x.py: Please rename this",
        "reviewed by bob",  # bob left no thread: the review is the feedback
    ]
    assert items["PR 2"] == ["reviewed by bob"]  # nothing open: the review stands


def test_ready_for_review_says_who_it_is_waiting_on() -> None:
    built = build(github=snapshot(mine=[pr(1, requested_users=["alice"], requested_teams=["web"])]))
    [item] = panel(built, "github.ready_for_review").items
    assert item.details == ["waiting on alice, team web"]
