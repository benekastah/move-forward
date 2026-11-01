"""The git-stack plugin: its Stacks panel, the stack entities it adds to git's branches, and
`git stack clean`."""

from pathlib import Path

import pytest
from conftest import (
    NOW,
    dashboard,
    iced,
    local_branch,
    local_snapshot,
    panel,
    pr,
    snapshot,
    stacks,
    stored,
    titles,
)
from legacy import save_json_snapshot

from move_forward import refresh
from move_forward.db import Database
from move_forward.graph import Ref
from move_forward.plugin import ActionError
from move_forward.plugins.git_stack import client
from move_forward.plugins.git_stack.models import StackBranch, StackRepo
from move_forward.plugins.git_stack.panels import repo_dirs
from move_forward.plugins.git_stack.repository import MIGRATIONS, StackRepository
from move_forward.registry import enabled

ICED = iced("me/x", "/src/widgets")


def test_stacks_panel_reports_unhealthy_and_pr_less_branches() -> None:
    built = dashboard(
        github=snapshot(mine=[pr(1), pr(2)]),
        local=local_snapshot(),
        stacks=stacks(
            StackBranch(name="main"),
            StackBranch(name="me/APP-1-branch", parent="main", status="up-to-date"),
            StackBranch(name="me/APP-2-branch", parent="me/APP-1-branch", status="needs-restack"),
            StackBranch(name="me/APP-3-branch", parent="main", status="merged"),
            StackBranch(name="me/no-pr", parent="main", status="up-to-date"),
        ),
    )
    assert sorted(titles(built, "git_stack.stacks")) == [
        "me/APP-2-branch: needs-restack",
        "me/APP-3-branch: merged",
        "me/no-pr: tracked, no PR",
    ]
    restack = next(i for i in panel(built, "git_stack.stacks").items if "restack" in i.title)
    assert restack.url == pr(2).url
    clean = panel(built, "git_stack.stacks").actions
    assert [(a.name, a.fields, a.label) for a in clean] == [
        ("git_stack/stack_clean", {"repo": "/src/widgets"}, "Clean 1 merged in widgets")
    ]
    assert clean[0].confirm is not None


def test_stack_clean_is_not_offered_while_the_repo_has_iced_stacks() -> None:
    built = dashboard(
        local=local_snapshot(branches=[local_branch("main"), local_branch("me/x")]),
        stacks=stacks(StackBranch(name="me/old", parent="main", status="merged")),
        iced_stacks=[ICED],
    )
    assert panel(built, "git_stack.stacks").actions == []
    assert "1 merged stack branches not cleaned" in titles(built, "git_stack.stacks")
    # Once git has deleted the iced branch, and git-stack doesn't track it, clean can't touch
    # it.
    gone = dashboard(
        stacks=stacks(StackBranch(name="me/old", parent="main", status="merged")),
        iced_stacks=[ICED],
    )
    assert [a.name for a in panel(gone, "git_stack.stacks").actions] == ["git_stack/stack_clean"]


def test_without_its_section_there_is_no_stacks_panel() -> None:
    assert "stacks" not in [p.key for p in dashboard(local=local_snapshot()).panels]


def test_tracked_branches_are_stack_branches_pointing_at_gits() -> None:
    config, db = stored(
        local=local_snapshot(branches=[local_branch("main"), local_branch("me/top", ahead=1)]),
        stacks=stacks(
            StackBranch(name="main"),
            StackBranch(name="me/top", parent="main", status="needs-restack"),
            StackBranch(name="me/gone", parent="main"),  # git deleted it; git-stack tracks it
        ),
    )
    graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
    top = graph.get("stack_branch", "/src/widgets\tme/top")
    assert top is not None and top.refs == [Ref(kind="branch", id="/src/widgets\tme/top")]
    assert top.attrs == {
        "repo_dir": "/src/widgets",
        "name": "me/top",
        "stack_parent": "main",
        "stack_status": "needs-restack",
    }
    # git's own branch is left as git published it, and a branch git deleted isn't git's.
    branch = graph.get("branch", "/src/widgets\tme/top")
    assert branch is not None and "stack_parent" not in branch.attrs
    assert graph.get("branch", "/src/widgets\tme/gone") is None
    assert graph.get("stack_branch", "/src/widgets\tmain") is None  # a root, not tracked
    assert repo_dirs(graph) == ["/src/widgets"]


def test_parse_info_reads_git_stack_json() -> None:
    out = (
        '[{"name": "main", "parent": null, "status": null, "current": false,'
        ' "reverted_parent": null, "group": null, "pr": null},'
        ' {"name": "me/a", "parent": "main", "status": "up-to-date", "current": true,'
        ' "reverted_parent": null, "group": "g", "pr": null}]'
    )
    assert client.parse_info(out) == [
        StackBranch(name="main"),
        StackBranch(name="me/a", parent="main", status="up-to-date", current=True, group="g"),
    ]


def test_stack_clean_refuses_unknown_repos_and_repos_with_iced_stacks() -> None:
    config, db = stored(
        local=local_snapshot(branches=[local_branch("main")]),
        stacks=stacks(StackBranch(name="me/x", parent="main")),  # git-stack still tracks it
        iced_stacks=[ICED],
    )
    with pytest.raises(ActionError, match=r"iced stacks \(me/x\)"):
        refresh.run_action(config, db, "git_stack/stack_clean", {"repo": "/src/widgets"})
    with pytest.raises(ActionError, match="not a configured repo"):
        refresh.run_action(config, db, "git_stack/stack_clean", {"repo": "/elsewhere"})


def test_collects_in_the_repos_git_has_branches_in(monkeypatch: pytest.MonkeyPatch) -> None:
    config, db = stored(local=local_snapshot(branches=[local_branch("main")]), stacks=stacks())
    plugins = enabled(config, db, NOW)
    graph, _ = refresh.stored_graph(db, plugins)
    asked: list[Path] = []

    def collect_repo(repo: Path) -> StackRepo:
        asked.append(repo)
        return StackRepo(dir=str(repo), branches=[])

    monkeypatch.setattr(client, "collect_repo", collect_repo)
    git_stack = next(e for e in plugins if e.name == "git_stack")
    collected = git_stack.plugin.collect_after(git_stack.ctx, graph)
    assert asked == [Path("/src/widgets")]
    assert [r.dir for r in collected.repos] == [str(Path("/src/widgets"))]


def test_git_stack_offers_to_untrack_iced_branches_it_tracks() -> None:
    built = dashboard(
        local=local_snapshot(branches=[local_branch("main"), local_branch("me/x")]),
        stacks=stacks(StackBranch(name="main"), StackBranch(name="me/x", parent="main")),
        iced_stacks=[iced("me/x")],
    )
    [item] = panel(built, "notes.iced").items
    [untrack] = [a for a in item.actions if a.name == "git_stack/untrack"]
    assert untrack.fields == {"targets": "/src/widgets\tme/x"} and untrack.confirm
    untracked = dashboard(local=local_snapshot(), stacks=stacks(), iced_stacks=[iced("me/x")])
    assert "git_stack/untrack" not in [
        a.name for a in panel(untracked, "notes.iced").items[0].actions
    ]


def test_untrack_takes_only_branches_git_stack_tracks() -> None:
    config, db = stored(
        local=local_snapshot(branches=[local_branch("main")]),
        stacks=stacks(StackBranch(name="main"), StackBranch(name="me/x", parent="main")),
    )
    for target in ("/src/widgets\t*", "/src/widgets\tmain", "/elsewhere\tme/x"):
        with pytest.raises(ActionError, match="doesn't track"):
            refresh.run_action(config, db, "git_stack/untrack", {"targets": target})


def test_the_migration_keeps_the_last_snapshot_and_store_replaces_it() -> None:
    db = Database(":memory:")
    first = stacks(
        StackBranch(name="main"),
        StackBranch(name="me/a", parent="main", status="needs-restack", current=True, group="g"),
    )
    save_json_snapshot(db, "git_stack", first)
    db.migrate("git_stack", MIGRATIONS)
    repo = StackRepository(db)
    assert repo.load() == first
    second = stacks(StackBranch(name="main"), dir="/src/other")
    repo.replace(second)
    assert repo.load() == second


def test_an_iced_branch_git_has_deleted_can_still_be_untracked() -> None:
    built = dashboard(
        local=local_snapshot(branches=[local_branch("main")]),  # me/x is gone from git
        stacks=stacks(StackBranch(name="main"), StackBranch(name="me/x", parent="main")),
        iced_stacks=[iced("me/x")],
    )
    [item] = panel(built, "notes.iced").items
    names = [a.name for a in item.actions]
    assert "git_stack/untrack" in names and "git/delete_together" not in names
    [untrack] = [a for a in item.actions if a.name == "git_stack/untrack"]
    assert untrack.fields == {"targets": "/src/widgets\tme/x"} and "me/x" in (untrack.confirm or "")


def test_iced_stack_branches_are_left_out_of_the_stacks_panel() -> None:
    built = dashboard(
        stacks=stacks(
            StackBranch(name="me/x", parent="main", status="needs-restack"),
            StackBranch(name="me/y", parent="main", status="needs-restack"),
        ),
        iced_stacks=[ICED],
    )
    assert sorted(titles(built, "git_stack.stacks")) == [
        "me/y: needs-restack",
        "me/y: tracked, no PR",
    ]
