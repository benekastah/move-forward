from datetime import UTC, datetime, timedelta

from conftest import ME, NOW, REPO, approved_review, event, pr, snapshot, thread

from move_forward_dashboard import panels
from move_forward_dashboard.domain import (
    Branch,
    Check,
    DerivedSnapshot,
    IcedBranch,
    IcedStack,
    JiraIssue,
    JiraSnapshot,
    LocalRepo,
    LocalSnapshot,
    Ping,
    ReviewerSuggestion,
    ReviewerUser,
    StackBranch,
    TeamChannel,
    TeamPrScore,
    Todo,
    WorkItem,
    Worktree,
)


def build(**overrides: object) -> panels.Dashboard:
    fields: dict[str, object] = {"now": NOW, "login": ME, "my_teams": ["core"]}
    fields.update(overrides)
    return panels.build(panels.Inputs.model_validate(fields))


def panel(dashboard: panels.Dashboard, key: str) -> panels.Panel:
    return next(p for p in dashboard.panels if p.key == key)


def titles(dashboard: panels.Dashboard, key: str) -> list[str]:
    return [item.title for item in panel(dashboard, key).items]


def issue(key: str = "APP-1", **overrides: object) -> JiraIssue:
    fields: dict[str, object] = {
        "key": key,
        "summary": "Do the thing",
        "status": "In Progress",
        "status_category": "indeterminate",
        "type": "Task",
        "created": NOW - timedelta(days=10),
        "updated": NOW - timedelta(days=1),
        "url": f"https://jira/browse/{key}",
    }
    fields.update(overrides)
    return JiraIssue.model_validate(fields)


def work_item(title: str, todos: list[Todo], **fields: list[str]) -> WorkItem:
    return WorkItem(title=title, fields=fields, todos=todos, text=title + " " + str(fields))


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
    assert sorted(titles(dashboard, "reviews_for_me")) == ["PR 1", "PR 2", "PR 4"]
    details = {i.title: i.details for i in panel(dashboard, "reviews_for_me").items}
    assert details["PR 4"] == ["review re-requested"]
    assert details["PR 2"][0].startswith("pushed")


def test_drafts_sort_last_in_every_panel() -> None:
    newest_draft = pr(1, author="alice", is_draft=True, updated_at=NOW)
    older = pr(2, author="alice", updated_at=NOW - timedelta(days=3))
    dashboard = build(github=snapshot(direct_requests=[newest_draft, older]))
    assert titles(dashboard, "reviews_for_me") == ["PR 2", "PR 1"]
    assert [i.draft for i in panel(dashboard, "reviews_for_me").items] == [False, True]


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
    items = {i.title: i for i in panel(dashboard, "needs_reviewer").items}
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
    items = {i.title: i for i in panel(dashboard, "feedback").items}
    assert set(items) == {"PR 1", "PR 2"}
    assert items["PR 1"].details == ["alice on src/x.py: Please rename this"]
    assert [link.text for link in items["PR 1"].links] == ["thread 1"]
    assert items["PR 2"].details == ["changes requested", "issuecomment by alice"]


def test_feedback_marks_outdated_threads() -> None:
    dashboard = build(github=snapshot(mine=[pr(1, threads=[thread(outdated=True)])]))
    assert panel(dashboard, "feedback").items[0].details[0].startswith("(outdated) ")


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
    items = {i.title: i for i in panel(dashboard, "ci_failures").items}
    assert set(items) == {"PR 1", "PR 2"}
    assert items["PR 1"].details == ["tests: FAILURE"]
    assert items["PR 1"].links[0].url == "https://ci/1"
    assert items["PR 2"].details == ["removed from merge queue 3h ago: CI failed"]


def test_conflicts() -> None:
    dashboard = build(
        github=snapshot(mine=[pr(1, mergeable="CONFLICTING"), pr(2, merge_state="DIRTY"), pr(3)])
    )
    assert sorted(titles(dashboard, "conflicts")) == ["PR 1", "PR 2"]


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
    condition = work_item(
        "APP-2 thing",
        [],
        **{"Merge conditions": ["don't merge until PR 1 is in"], "Where": [held.url]},
    )
    dashboard = build(
        github=snapshot(mine=[ready, held, stacked, threads, pending, queued, unapproved]),
        work_items=[condition],
    )
    assert titles(dashboard, "ready_to_merge") == ["PR 1"]
    assert titles(dashboard, "held") == ["PR 2"]
    assert panel(dashboard, "held").items[0].details == ["held: don't merge until PR 1 is in"]


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
        work_items=[work_item("APP-5 x", [Todo(text="finish", size="big")])],
        placeholder_labels=["needs-team", "needs-risk"],
        ticket_link_prefix="Link",
    )
    items = {i.title: i.details for i in panel(dashboard, "metadata").items}
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
    assert titles(dashboard, "team_prs") == ["PR 2", "PR 3", "PR 4"]
    assert panel(dashboard, "team_prs").items[0].details == [
        "2 files owned by my teams (1 required), 1 in dirs I work in"
    ]
    assert "waiting on core" in panel(dashboard, "team_prs").description


# Local


def _local(**overrides: object) -> LocalSnapshot:
    fields: dict[str, object] = {
        "dir": "/src/widgets",
        "github_repo": REPO,
        "default_branch": "main",
        "worktrees": [],
        "branches": [],
    }
    fields.update(overrides)
    return LocalSnapshot(repos=[LocalRepo.model_validate(fields)])


def _branch(name: str, **overrides: object) -> Branch:
    fields: dict[str, object] = {"name": name, "committed_at": NOW - timedelta(days=1)}
    fields.update(overrides)
    return Branch.model_validate(fields)


def test_local_work_shows_dirty_worktrees_and_unpushed_branches() -> None:
    local = _local(
        worktrees=[
            Worktree(path="/src/widgets", branch="me/APP-1-branch", modified=2, untracked=1),
            Worktree(path="/src/widgets2", branch="other", modified=0, untracked=0),
        ],
        branches=[
            _branch("main"),
            _branch("me/APP-1-branch", upstream="origin/me/APP-1-branch", ahead=3),
            _branch("me/new", ahead_of_default=2),
            _branch("me/iced", ahead_of_default=1),
            _branch("me/synced", upstream="origin/me/synced"),
        ],
    )
    dashboard = build(
        github=snapshot(mine=[pr(1)]),
        local=local,
        iced_stacks=[
            IcedStack(
                bottom_branch="me/iced",
                repo_dir="x",
                branches=[IcedBranch(name="me/iced", sha="1")],
            )
        ],
    )
    items = {i.title: i for i in panel(dashboard, "local_work").items}
    assert set(items) == {
        "me/APP-1-branch: 2 modified, 1 untracked",
        "me/APP-1-branch: 3 unpushed commits",
        "me/new: 2 commits, never pushed",
        "me/iced: 1 commits, never pushed",
    }
    assert items["me/APP-1-branch: 3 unpushed commits"].url == pr(1).url
    assert items["me/APP-1-branch: 3 unpushed commits"].badges == ["has PR"]
    assert items["me/iced: 1 commits, never pushed"].badges == ["iced", "no PR"]
    actions = items["me/APP-1-branch: 3 unpushed commits"].actions
    assert [(a.name, a.fields) for a in actions if a.name != "todo_add"] == [
        ("push_branch", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
        ("ice", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
    ]
    assert [a.name for a in items["me/iced: 1 commits, never pushed"].actions] == ["push_branch"]
    dirty = items["me/APP-1-branch: 2 modified, 1 untracked"].actions
    assert [(a.name, a.fields) for a in dirty if a.name != "todo_add"] == [
        ("wip_commit", {"repo": "/src/widgets", "worktree": "/src/widgets"}),
        ("ice", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
    ]


def test_stacks_panel_reports_unhealthy_and_pr_less_branches() -> None:
    local = _local(
        stack=[
            StackBranch(name="main"),
            StackBranch(name="me/APP-1-branch", parent="main", status="up-to-date"),
            StackBranch(name="me/APP-2-branch", parent="me/APP-1-branch", status="needs-restack"),
            StackBranch(name="me/APP-3-branch", parent="main", status="merged"),
            StackBranch(name="me/no-pr", parent="main", status="up-to-date"),
        ]
    )
    dashboard = build(github=snapshot(mine=[pr(1), pr(2)]), local=local)
    assert sorted(titles(dashboard, "stacks")) == [
        "me/APP-2-branch: needs-restack",
        "me/APP-3-branch: merged",
        "me/no-pr: tracked, no PR",
    ]
    clean = panel(dashboard, "stacks").actions
    assert [(a.name, a.fields, a.label) for a in clean] == [
        ("stack_clean", {"repo": "/src/widgets"}, "Clean 1 merged in widgets")
    ]
    assert clean[0].confirm is not None


def test_stack_clean_is_not_offered_while_the_repo_has_iced_stacks() -> None:
    local = _local(stack=[StackBranch(name="me/old", parent="main", status="merged")])
    dashboard = build(
        local=local,
        iced_stacks=[
            IcedStack(
                bottom_branch="me/x",
                repo_dir="/src/widgets",
                branches=[IcedBranch(name="me/x", sha="1")],
            )
        ],
    )
    stacks = panel(dashboard, "stacks")
    assert stacks.actions == []
    assert "1 merged stack branches not cleaned" in titles(dashboard, "stacks")


def test_cleanup_lists_merged_and_gone_branches() -> None:
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")
    local = _local(
        branches=[
            _branch("me/done", upstream="origin/me/done", gone=True),
            _branch("me/gone", upstream="origin/me/gone", gone=True),
            _branch("me/gone-but-ahead", upstream="origin/x", gone=True, ahead=1),
            _branch("me/live", upstream="origin/me/live"),
        ]
    )
    dashboard = build(github=snapshot(merged_recent=[merged]), local=local)
    cleanup = panel(dashboard, "cleanup")
    assert titles(dashboard, "cleanup") == ["me/done: PR merged 1d ago", "me/gone: upstream gone"]
    assert [a.fields for item in cleanup.items for a in item.actions] == [
        {"repo": "/src/widgets", "branch": "me/done"},
        {"repo": "/src/widgets", "branch": "me/gone"},
    ]
    assert cleanup.actions[0].name == "delete_branches"
    assert cleanup.actions[0].fields["targets"] == "/src/widgets\tme/done\n/src/widgets\tme/gone"
    assert cleanup.actions[0].confirm is not None


def test_branch_prefixes_limit_cleanup_and_local_work_to_my_branches() -> None:
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="theirs/merged-by-me")
    local = _local(
        branches=[
            _branch("me/gone", upstream="origin/me/gone", gone=True),
            _branch("theirs/gone", upstream="origin/theirs/gone", gone=True),
            _branch("theirs/merged-by-me", upstream="origin/x", gone=True),
            _branch("theirs/unpushed", ahead_of_default=3),
            _branch("me/unpushed", ahead_of_default=3),
        ]
    )
    dashboard = build(
        github=snapshot(merged_recent=[merged]), local=local, my_branch_prefixes=["me/"]
    )
    assert sorted(titles(dashboard, "cleanup")) == [
        "me/gone: upstream gone",
        "theirs/merged-by-me: PR merged 1d ago",
    ]
    assert titles(dashboard, "local_work") == ["me/unpushed: 3 commits, never pushed"]


# Pings and nudges


def _teams() -> dict[str, TeamChannel]:
    return {"web": TeamChannel(slug="web", channel_name="#team-web", channel_id="C1")}


def _users() -> dict[str, ReviewerUser]:
    return {"alice": ReviewerUser(login="alice", email="a@x", slack_id="U1")}


def test_ping_targets_links_and_messages() -> None:
    candidate = pr(1, requested_users=["alice", "bob"], requested_teams=["web", "unknown"])
    dashboard = build(
        github=snapshot(mine=[candidate]),
        users=_users(),
        teams=_teams(),
        slack_workspace_url="https://acme.slack.com/",
    )
    ping = dashboard.pings[candidate.url]
    assert ping.blockers == []
    by_name = {t.name: t for t in ping.targets}
    assert by_name["alice"].slack_link == "https://acme.slack.com/team/U1"
    assert by_name["bob"].slack_link is None and by_name["bob"].destination == "bob (DM)"
    assert by_name["web"].slack_link == "https://acme.slack.com/archives/C1"
    assert by_name["web"].destination == "#team-web (C1)"
    assert by_name["unknown"].destination == "team unknown"
    assert by_name["alice"].message == panels.FIRST_PING.format(url=candidate.url)


def test_follow_up_after_a_recorded_ping_and_skill_nudges_count_for_all_targets() -> None:
    candidate = pr(1, requested_users=["alice"], requested_teams=["web"])
    other = pr(2, requested_users=["alice"])
    dashboard = build(
        github=snapshot(mine=[candidate, other]),
        pings=[
            Ping(
                pr_url=candidate.url,
                target="user:alice",
                kind="first",
                sent_at=NOW - timedelta(days=1),
            ),
            Ping(pr_url=other.url, target="unknown (from work notes)", kind="first", sent_at=NOW),
        ],
    )
    targets = {t.name: t for t in dashboard.pings[candidate.url].targets}
    assert targets["alice"].ping_kind == "follow-up"
    assert targets["alice"].message == panels.FOLLOW_UP_PING
    assert targets["web"].ping_kind == "first"
    assert dashboard.pings[other.url].targets[0].ping_kind == "follow-up"


def test_nudge_blockers_and_staleness() -> None:
    fresh = pr(
        1,
        requested_users=["alice"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(hours=2), reviewer="alice")],
    )
    stale = pr(
        2,
        requested_users=["alice"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(days=2), reviewer="alice")],
    )
    policy_only = pr(
        3,
        requested_teams=["web"],
        checks=[Check(name="review-policy/owners", state="FAILURE")],
    )
    broken = pr(4, requested_users=["alice"], checks=[Check(name="tests", state="FAILURE")])
    threads = pr(5, requested_users=["alice"], threads=[thread()])
    approved = pr(6, requested_users=["alice"], reviews=[approved_review()])
    nobody = pr(7)
    draft = pr(8, is_draft=True, requested_users=["alice"])
    dashboard = build(
        github=snapshot(mine=[fresh, stale, policy_only, broken, threads, approved, nobody, draft]),
        review_checks=["review-policy/owners"],
    )
    pings = dashboard.pings
    assert pings[fresh.url].blockers == [] and not pings[fresh.url].stale
    assert pings[stale.url].stale
    assert pings[policy_only.url].blockers == []
    assert pings[broken.url].blockers == ["failing checks"]
    assert pings[threads.url].blockers == ["unresolved threads"]
    assert pings[approved.url].blockers == ["already approved"]
    assert pings[nobody.url].blockers == ["no reviewers requested"]
    assert draft.url not in pings
    assert sorted(titles(dashboard, "nudges")) == ["PR 2", "PR 3"]


def test_iced_prs_are_left_out_of_my_pr_panels() -> None:
    iced = pr(1, requested_users=["alice"], mergeable="CONFLICTING", threads=[thread()])
    dashboard = build(
        github=snapshot(mine=[iced]),
        iced_stacks=[
            IcedStack(
                bottom_branch=iced.head,
                repo_dir="x",
                branches=[IcedBranch(name=iced.head, sha="1")],
            )
        ],
    )
    for key in ("conflicts", "feedback", "nudges", "needs_reviewer", "metadata"):
        assert titles(dashboard, key) == []
    assert iced.url not in dashboard.pings
    assert titles(dashboard, "iced") == [iced.head]
    actions = panel(dashboard, "iced").items[0].actions
    assert [(a.name, a.label) for a in actions] == [
        ("thaw", "Reopen"),
        ("delete_iced", "Delete the work"),
    ]
    assert actions[1].confirm is not None and iced.head in actions[1].confirm


# Jira and todos


def test_jira_transitions_from_pr_states() -> None:
    merged = pr(1, state="MERGED", merged_at=NOW)
    open_pr = pr(2)
    draft = pr(3, is_draft=True)
    dashboard = build(
        github=snapshot(mine=[open_pr, draft], merged_recent=[merged]),
        jira=JiraSnapshot(
            issues=[
                issue("APP-1", status="In Review"),
                issue("APP-2", status="In Progress"),
                issue("APP-3", status="In Review"),
                issue("APP-4", status="In Progress"),
                issue("APP-5", status="Accepted", status_category="new"),
            ]
        ),
    )
    subtitles = {i.title.split()[0]: i.subtitle for i in panel(dashboard, "jira_transitions").items}
    assert subtitles == {
        "APP-1": "In Review → Closed: all PRs merged",
        "APP-2": "In Progress → In Review: all PRs open and non-draft",
        "APP-3": "In Review → In Progress: a PR is draft or has changes requested",
    }


def test_jira_next_ranks_incidents_then_bugs_then_unblocked_work() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[
                issue("APP-1", type="Task"),
                issue("APP-2", type="Task", open_blockers=["APP-9"]),
                issue("APP-3", type="Bug"),
                issue("APP-4", type="Incident"),
                issue("APP-5", type="Incident", labels=["esc-vp"]),
                issue("APP-6", type="Bug", labels=["esc-manager"]),
            ]
        ),
        escalation_label_prefix="esc-",
        escalation_levels=["manager", "director", "vp"],
    )
    keys = [i.title.split()[0] for i in panel(dashboard, "jira_next").items]
    assert keys == ["APP-5", "APP-4", "APP-6", "APP-3", "APP-1", "APP-2"]
    badges = {i.title.split()[0]: i.badges for i in panel(dashboard, "jira_next").items}
    assert badges["APP-5"] == ["incident, escalated (3)"]
    assert badges["APP-2"] == ["blocked by APP-9"]


def test_todos_put_close_outs_first_and_blocked_last() -> None:
    close_out = work_item("APP-1 almost", [Todo(text="reply", size="quick")])
    mixed = work_item(
        "APP-2 mixed", [Todo(text="big one", size="big"), Todo(text="small", size="quick")]
    )
    blocked = work_item(
        "APP-3 stuck", [Todo(text="x", size="quick")], **{"Blocked on": ["reviews"]}
    )
    unblocked = work_item(
        "APP-4 fine", [Todo(text="x", size="quick")], **{"Blocked on": ["nothing"]}
    )
    done = work_item("APP-5 done", [])
    dashboard = build(work_items=[mixed, blocked, close_out, done, unblocked])
    items = panel(dashboard, "todos").items
    assert [i.title for i in items] == ["APP-1 almost", "APP-4 fine", "APP-2 mixed", "APP-3 stuck"]
    assert items[0].badges == ["close-out: 1 quick todo(s)"]
    assert items[2].details == ["[quick] small", "[big] big one"]
    assert items[3].badges == ["close-out: 1 quick todo(s)", "blocked: reviews"]


def test_every_panel_is_in_exactly_one_group() -> None:
    dashboard = build(local=_local(stack=[StackBranch(name="main")]))
    grouped = [key for group in dashboard.groups for key in group.panel_keys]
    assert sorted(grouped) == sorted(p.key for p in dashboard.panels)
    assert dashboard.panel("feedback").key == "feedback"


def test_stacks_panel_only_exists_with_git_stack_data() -> None:
    assert build().find_panel("stacks") is None
    assert build(local=_local()).find_panel("stacks") is None
    assert build(local=_local(stack=[StackBranch(name="main")])).find_panel("stacks") is not None


def test_age() -> None:
    assert panels.age(NOW, NOW - timedelta(minutes=5)) == "5m"
    assert panels.age(NOW, NOW - timedelta(hours=5)) == "5h"
    assert panels.age(NOW, NOW - timedelta(days=5)) == "5d"
    assert panels.age(NOW, None) == "?"
    assert panels.age(NOW, datetime(2026, 10, 29, 12)) == "1d"  # naive counts as UTC


def test_build_with_nothing_stored() -> None:
    dashboard = build()
    assert all(p.items == [] for p in dashboard.panels)
    assert dashboard.generated_at == datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_focus_order_covers_every_panel_in_the_skills_order() -> None:
    dashboard = build(
        local=LocalSnapshot(
            repos=[
                LocalRepo(
                    dir=REPO,
                    github_repo="acme/widgets",
                    default_branch="main",
                    worktrees=[],
                    branches=[],
                    stack=[StackBranch(name="main")],
                )
            ]
        )
    )
    assert {p.key for p in dashboard.panels} <= set(panels.FOCUS_ORDER)
    assert len(set(panels.FOCUS_ORDER)) == len(panels.FOCUS_ORDER)
    assert panels.FOCUS_ORDER.index("reviews_for_me") < panels.FOCUS_ORDER.index("feedback")
    assert panels.FOCUS_ORDER.index("cleanup") < panels.FOCUS_ORDER.index("iced")


def test_focus_items_by_category() -> None:
    dashboard = build(
        github=snapshot(
            mine=[pr(1, requested_users=["alice"]), pr(2, merge_state="DIRTY")],
            direct_requests=[pr(3, author="bob")],
        )
    )
    everything = dashboard.focus_items(panels.FOCUS_ALL)
    assert everything is not None
    keys = [panel.key for panel, _ in everything]
    assert keys.index("reviews_for_me") < keys.index("conflicts") < keys.index("needs_reviewer")
    assert [i.title for _, i in everything if _.key == "reviews_for_me"] == [pr(3).title]
    act = dashboard.focus_items("act")
    assert act is not None and [panel.key for panel, _ in act] == ["reviews_for_me", "conflicts"]
    one = dashboard.focus_items("nudges")
    assert one is not None and [i.title for _, i in one] == [pr(1).title]
    assert dashboard.focus_items("nope") is None
    assert dashboard.focus_title("act") == "Act now"
    assert dashboard.focus_title(panels.FOCUS_ALL) == "All"
    assert dashboard.focus_title("nope") is None


def test_focus_shows_todos_one_at_a_time() -> None:
    mixed = work_item(
        "APP-2 mixed",
        [Todo(text="big one", size="big"), Todo(text="small", size="quick")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    blocked = work_item("APP-3 stuck", [Todo(text="x")], **{"Blocked on": ["reviews"]})
    dashboard = build(work_items=[mixed, blocked])
    assert titles(dashboard, "todos") == ["APP-2 mixed", "APP-3 stuck"]  # the page is unchanged
    focused = dashboard.focus_items("todos")
    assert focused is not None
    assert [(i.title, i.subtitle, i.badges) for _, i in focused] == [
        ("small", "APP-2 mixed", ["quick"]),
        ("big one", "APP-2 mixed", ["big"]),
        ("x", "APP-3 stuck", ["unsized", "blocked: reviews"]),
    ]
    assert focused[0][1].url is None  # the todo doesn't say which PR it's about
    assert [link.text for link in focused[0][1].links] == ["PR 7"]


def test_todo_parts_carry_their_actions_and_a_done_todo_can_be_rebuilt() -> None:
    entry = work_item(
        "APP-2 mixed",
        [Todo(text="small", size="quick")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    dashboard = build(work_items=[entry])
    focused = dashboard.focus_items("todos")
    assert focused is not None
    [(_, part)] = focused
    assert [(a.name, a.checkbox, a.checked) for a in part.actions] == [
        ("todo_done", True, False),
        ("todo_delete", False, False),
        ("todo_link", False, False),
        ("todo_add", False, False),
    ]
    assert part.actions[0].fields == {"entry": "APP-2 mixed", "text": "small"}
    assert part.actions[1].confirm is not None and "small" in part.actions[1].confirm
    # The entry on the page only gets the Add todo field, not the todo's own actions.
    assert [a.name for a in panel(dashboard, "todos").items[0].actions] == ["todo_add"]

    done = dashboard.done_todo("APP-2 mixed", "small")
    assert (done.title, done.subtitle, done.badges) == ("small", "APP-2 mixed", ["done"])
    assert done.url == "https://github.com/acme/widgets/pull/7"
    assert [(a.name, a.checked) for a in done.actions] == [("todo_undone", True)]
    assert dashboard.done_todo("gone entry", "x").url is None


def test_todo_parts_put_the_prs_they_mention_first_in_bold() -> None:
    urls = [f"https://github.com/acme/widgets/pull/{n}" for n in (11, 222, 3333)]
    entry = work_item(
        "APP-2",
        [
            Todo(text="get it merged"),
            Todo(text="reply on #222 then rebase 3333"),
            Todo(text=f"check {urls[0]} plans; 1222 is not a PR"),
            Todo(text="follow up on 3333"),
        ],
        Where=urls,
    )
    github = snapshot(
        mine=[pr(222)], merged_recent=[pr(11, state="MERGED")], closed_recent=[pr(3333)]
    )
    focused = build(work_items=[entry], github=github).focus_items("todos")
    assert focused is not None
    parts = [part for _, part in focused]
    assert [link.pr_state for link in parts[0].links] == ["merged", "open", "closed"]
    # Only the todo whose every mentioned PR is merged or closed is suggested as done.
    assert [p.actions[0].suggested for p in parts] == [False, False, True, True]
    assert parts[2].badges == ["unsized", "its PRs are done"]
    assert parts[1].badges == ["unsized"]
    assert [(l.text, l.strong) for l in parts[0].links] == [
        ("PR 11", False),
        ("PR 222", False),
        ("PR 3333", False),
    ]
    assert parts[0].url is None
    assert [(l.text, l.strong) for l in parts[1].links] == [
        ("PR 222", True),
        ("PR 3333", True),
        ("PR 11", False),
    ]
    assert parts[1].url == urls[1]
    assert [(l.text, l.strong) for l in parts[2].links] == [
        ("PR 11", True),
        ("PR 222", False),
        ("PR 3333", False),
    ]


def test_todo_links_include_the_tickets_and_prs_only_the_todo_mentions() -> None:
    entry = work_item(
        "APP-2 thing",
        [Todo(text="ask about APP-9 and APP-9 again https://github.com/acme/other/pull/4")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    github = snapshot(merged_recent=[pr(7, state="MERGED")])
    dashboard = build(work_items=[entry], github=github, jira_site="https://j.example/")
    focused = dashboard.focus_items("todos")
    assert focused is not None
    [(_, part)] = focused
    assert [(l.text, l.url, l.strong, l.pr_state) for l in part.links] == [
        ("PR 7", "https://github.com/acme/widgets/pull/7", False, "merged"),
        ("PR 4", "https://github.com/acme/other/pull/4", True, None),
        ("APP-9", "https://j.example/browse/APP-9", True, None),
    ]
    assert part.url == "https://github.com/acme/other/pull/4"
    # An unknown-state PR is not evidence the todo is done.
    assert part.actions[0].suggested is False
    assert [a.name for a in part.actions] == ["todo_done", "todo_delete", "todo_link", "todo_add"]
    assert part.actions[2].input == "Jira key or PR URL"
    # Without a Jira site, keys aren't linked.
    [(_, part)] = build(work_items=[entry]).focus_items("todos") or []
    assert [l.text for l in part.links] == ["PR 7", "PR 4"]


def test_my_prs_tickets_and_entries_get_an_add_todo_field() -> None:
    covered = pr(1, head="me/APP-1-x")  # APP-1 has an entry
    by_url = pr(2, head="me/no-key")
    fresh = pr(3, head="me/APP-3-y", title="Do y")
    no_key = pr(4, head="me/plain", title="Plain", body="no ticket")
    entries = [
        work_item("APP-1 Fix", [Todo(text="t")]),
        work_item("Some PR work", [Todo(text="u")], Where=[by_url.url]),
    ]
    dashboard = build(
        github=snapshot(
            mine=[covered, by_url, fresh, no_key], direct_requests=[pr(9, author="bob")]
        ),
        jira=JiraSnapshot(
            issues=[issue("APP-3", summary="Why not"), issue("APP-8", summary="Next")]
        ),
        work_items=entries,
    )

    def add(panel_key: str, title: str) -> dict[str, str]:
        item = next(i for i in panel(dashboard, panel_key).items if i.title == title)
        [action] = [a for a in item.actions if a.name == "todo_add"]
        assert action.input == panels.ADD_TODO_INPUT
        return action.fields

    assert add("needs_reviewer", covered.title) == {"entry": "APP-1 Fix"}
    assert add("needs_reviewer", by_url.title) == {"entry": "Some PR work"}
    assert add("needs_reviewer", "Do y") == {
        "entry": "APP-3 Why not",
        "create": "1",
        "where": fresh.url,
    }
    assert add("needs_reviewer", "Plain") == {
        "entry": f"{no_key.url} Plain",
        "create": "1",
        "where": no_key.url,
    }
    assert add("jira_next", "APP-8 Next") == {
        "entry": "APP-8 Next",
        "create": "1",
        "where": "https://jira/browse/APP-8",
    }
    assert add("todos", "APP-1 Fix") == {"entry": "APP-1 Fix"}
    # Other people's PRs don't get one.
    review = next(i for i in panel(dashboard, "reviews_for_me").items)
    assert [a.name for a in review.actions] == []


def test_naming_settings_drive_pings_labels_and_jira() -> None:
    dashboard = build(
        github=snapshot(
            mine=[pr(1, requested_users=["alice"], labels=["needs-team"], head="me/XY-1")]
        ),
        jira=JiraSnapshot(
            issues=[
                issue("XY-1", status="Doing"),
                issue("XY-2", type="Outage", labels=["esc:vp"]),
                issue("XY-3", type="Defect"),
            ]
        ),
        ping_first="Please look at {url}",
        ping_follow_up="Bump!",
        placeholder_labels=["needs-team"],
        jira_statuses=panels.JiraStatuses(
            in_progress="Doing", in_review="Checking", closed="Shipped"
        ),
        escalation_label_prefix="esc:",
        escalation_levels=["lead", "vp"],
        incident_types=["outage"],
        bug_types=["defect"],
    )
    [target] = dashboard.pings[pr(1).url].targets
    assert target.message == f"Please look at {pr(1).url}"
    assert titles(dashboard, "metadata") == [pr(1).title]
    assert "needs-team" in panel(dashboard, "metadata").items[0].details[0]
    [transition] = panel(dashboard, "jira_transitions").items
    assert transition.subtitle == "Doing → Checking: all PRs open and non-draft"
    by_key = {i.title.split()[0]: i.badges[0] for i in panel(dashboard, "jira_next").items}
    assert by_key["XY-2"].startswith("incident, escalated (2)")  # vp is the 2nd level
    assert by_key["XY-3"].startswith("bug")
