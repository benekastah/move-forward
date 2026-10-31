"""The git plugin's panels, built as core builds the page."""

from datetime import timedelta

from conftest import (
    NOW,
    dashboard,
    local_branch,
    local_snapshot,
    panel,
    pr,
    snapshot,
    titles,
)

from move_forward.domain import (
    IcedBranch,
    IcedStack,
    StackBranch,
    Worktree,
)

build = dashboard


# Local


def test_local_work_shows_dirty_worktrees_and_unpushed_branches() -> None:
    local = local_snapshot(
        worktrees=[
            Worktree(path="/src/widgets", branch="me/APP-1-branch", modified=2, untracked=1),
            Worktree(path="/src/widgets2", branch="other", modified=0, untracked=0),
        ],
        branches=[
            local_branch("main"),
            local_branch("me/APP-1-branch", upstream="origin/me/APP-1-branch", ahead=3),
            local_branch("me/new", ahead_of_default=2),
            local_branch("me/iced", ahead_of_default=1),
            local_branch("me/synced", upstream="origin/me/synced"),
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
    assert [(a.name, a.fields) for a in actions if a.name != "notes/todo_add"] == [
        ("git/push_branch", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
        ("notes/ice", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
    ]
    assert [a.name for a in items["me/iced: 1 commits, never pushed"].actions] == [
        "git/push_branch"
    ]
    dirty = items["me/APP-1-branch: 2 modified, 1 untracked"].actions
    assert [(a.name, a.fields) for a in dirty if a.name != "notes/todo_add"] == [
        ("git/wip_commit", {"repo": "/src/widgets", "worktree": "/src/widgets"}),
        ("notes/ice", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
    ]


def test_stacks_panel_reports_unhealthy_and_pr_less_branches() -> None:
    local = local_snapshot(
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
        ("git/stack_clean", {"repo": "/src/widgets"}, "Clean 1 merged in widgets")
    ]
    assert clean[0].confirm is not None


def test_stack_clean_is_not_offered_while_the_repo_has_iced_stacks() -> None:
    local = local_snapshot(stack=[StackBranch(name="me/old", parent="main", status="merged")])
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
    local = local_snapshot(
        branches=[
            local_branch("me/done", upstream="origin/me/done", gone=True),
            local_branch("me/gone", upstream="origin/me/gone", gone=True),
            local_branch("me/gone-but-ahead", upstream="origin/x", gone=True, ahead=1),
            local_branch("me/live", upstream="origin/me/live"),
        ]
    )
    dashboard = build(github=snapshot(merged_recent=[merged]), local=local)
    cleanup = panel(dashboard, "cleanup")
    assert titles(dashboard, "cleanup") == ["me/done: PR merged 1d ago", "me/gone: upstream gone"]
    assert [a.fields for item in cleanup.items for a in item.actions] == [
        {"repo": "/src/widgets", "branch": "me/done"},
        {"repo": "/src/widgets", "branch": "me/gone"},
    ]
    assert cleanup.actions[0].name == "git/delete_branches"
    assert cleanup.actions[0].fields["targets"] == "/src/widgets\tme/done\n/src/widgets\tme/gone"
    assert cleanup.actions[0].confirm is not None


def test_branch_prefixes_limit_cleanup_and_local_work_to_my_branches() -> None:
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="theirs/merged-by-me")
    local = local_snapshot(
        branches=[
            local_branch("me/gone", upstream="origin/me/gone", gone=True),
            local_branch("theirs/gone", upstream="origin/theirs/gone", gone=True),
            local_branch("theirs/merged-by-me", upstream="origin/x", gone=True),
            local_branch("theirs/unpushed", ahead_of_default=3),
            local_branch("me/unpushed", ahead_of_default=3),
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


def test_stacks_panel_only_exists_with_git_stack_data() -> None:
    assert build().find_panel("stacks") is None
    assert build(local=local_snapshot()).find_panel("stacks") is None
    assert (
        build(local=local_snapshot(stack=[StackBranch(name="main")])).find_panel("stacks")
        is not None
    )
