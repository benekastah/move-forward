"""The git plugin's panels, built as core builds the page."""

from datetime import timedelta

from conftest import (
    NOW,
    dashboard,
    iced,
    local_branch,
    local_snapshot,
    panel,
    pr,
    snapshot,
    stored,
    titles,
)
from legacy import save_json_snapshot

from move_forward import refresh
from move_forward.db import Database
from move_forward.plugins.git.models import Worktree
from move_forward.plugins.git.repository import MIGRATIONS, LocalRepository
from move_forward.registry import enabled

build = dashboard


def test_branches_are_published_with_their_checkout() -> None:
    config, db = stored(
        local=local_snapshot(branches=[local_branch("me/a", ahead=2, sha="c0ffee")])
    )
    graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
    branch = graph.get("branch", "/src/widgets\tme/a")
    assert branch is not None
    assert branch.attrs["repo_dir"] == "/src/widgets" and branch.attrs["ahead"] == 2
    assert branch.attrs["sha"] == "c0ffee"


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
        iced_stacks=[iced("me/iced", "/src/widgets")],
    )
    items = {i.title: i for i in panel(dashboard, "git.local_work").items}
    assert set(items) == {
        "me/APP-1-branch: 2 modified, 1 untracked",
        "me/APP-1-branch: 3 unpushed commits",
        "me/new: 2 commits, never pushed",
    }  # me/iced is on ice: hidden
    assert items["me/APP-1-branch: 3 unpushed commits"].url == pr(1).url
    assert items["me/APP-1-branch: 3 unpushed commits"].badges == ["has PR"]
    actions = items["me/APP-1-branch: 3 unpushed commits"].actions
    assert [(a.name, a.fields) for a in actions if a.name != "notes/todo_add"] == [
        ("git/push_branch", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
        ("notes/ice", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
    ]
    assert [a.name for a in items["me/new: 2 commits, never pushed"].actions] == [
        "git/push_branch",
        "notes/ice",
    ]
    dirty = items["me/APP-1-branch: 2 modified, 1 untracked"].actions
    assert [(a.name, a.fields) for a in dirty if a.name != "notes/todo_add"] == [
        ("git/wip_commit", {"repo": "/src/widgets", "worktree": "/src/widgets"}),
        ("notes/ice", {"repo": "/src/widgets", "branch": "me/APP-1-branch"}),
    ]


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
    cleanup = panel(dashboard, "git.cleanup")
    assert titles(dashboard, "git.cleanup") == [
        "me/done: PR merged 1d ago",
        "me/gone: upstream gone",
    ]
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
    assert sorted(titles(dashboard, "git.cleanup")) == [
        "me/gone: upstream gone",
        "theirs/merged-by-me: PR merged 1d ago",
    ]
    assert titles(dashboard, "git.local_work") == ["me/unpushed: 3 commits, never pushed"]


def test_a_branch_iced_in_another_repo_is_not_iced_here() -> None:
    local = local_snapshot(branches=[local_branch("main"), local_branch("me/x", ahead=1)])
    elsewhere = iced("me/x", "/src/other")
    [item] = panel(build(local=local, iced_stacks=[elsewhere]), "git.local_work").items
    assert "iced" not in item.badges
    assert "notes/ice" in [a.name for a in item.actions]


def test_git_offers_to_delete_iced_branches_on_the_on_ice_item_only() -> None:
    local = local_snapshot(branches=[local_branch("main"), local_branch("me/x", ahead=1)])
    built = build(local=local, iced_stacks=[iced("me/x"), iced("me/gone")])
    on_ice = {item.title: item for item in panel(built, "notes.iced").items}
    [delete] = [a for a in on_ice["me/x"].actions if a.name == "git/delete_together"]
    assert delete.fields == {"targets": "/src/widgets\tme/x"} and delete.confirm
    # A branch git no longer has can't be deleted again.
    assert [a.name for a in on_ice["me/gone"].actions] == ["notes/thaw"]
    assert panel(built, "git.local_work").items == []  # me/x is on ice: hidden


def test_git_keeps_its_snapshot_in_its_tables() -> None:
    first = local_snapshot(
        worktrees=[Worktree(path="/src/widgets", branch=None, modified=1, untracked=2)],
        branches=[
            local_branch("main", sha="a" * 40, upstream="origin/main"),
            local_branch("me/x", ahead=2, gone=True, ahead_of_default=3),
        ],
        my_dirs=["api", "web"],
    )
    db = Database(":memory:")
    save_json_snapshot(db, "git", first)
    db.migrate("git", MIGRATIONS)
    repo = LocalRepository(db)
    assert repo.load() == first
    second = local_snapshot(dir="/src/other", github_repo=None)
    repo.replace(second)
    assert repo.load() == second
    assert db.query("SELECT * FROM git_branches") == []  # gone with their repo


def test_a_merged_branch_still_here_is_nominated_to_delete() -> None:
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")
    local = local_snapshot(branches=[local_branch("me/done", upstream="origin/me/done")])
    act = panel(build(github=snapshot(merged_recent=[merged]), local=local), "act.now").items
    assert [(i.title, i.details[0]) for i in act] == [
        ("Delete the branch: me/done: PR merged 1d ago", "why: quick")
    ]
