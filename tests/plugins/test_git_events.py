"""What git and git_stack notifications say."""

from datetime import timedelta
from typing import cast

from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stacks, stored

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import GraphEventsRequest
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git_stack import events
from move_forward.plugins.git_stack.models import StackBranch
from move_forward.plugins.git_stack.plugin import GitStackPlugin
from move_forward.plugins.github.snapshots import PrRepository


class _Refreshes:
    """git's `graph_events` over what's stored, as successive refreshes ask it: each against
    what the one before saw (the first against nothing)."""

    def __init__(self, pair: tuple[Config, Database]):
        self.pair = pair
        self.previous: tuple[Graph, LocalSnapshot | None] | None = None

    def deletable(self) -> list[str]:
        config, db = self.pair
        enabled = registry.enabled(config, db, NOW)
        graph, snapshots = refresh.stored_graph(db, enabled)
        git = next(e for e in enabled if e.name == "git")
        now = cast(LocalSnapshot | None, snapshots["git"])
        before_graph, before = self.previous or (graph, None)
        req = GraphEventsRequest(git.ctx, graph, now, before_graph, before)
        self.previous = (graph, now)
        return [e.title for e in GitPlugin().graph_events(req)]


def test_a_branch_whose_pr_merges_is_deletable_once_and_not_the_first_time() -> None:
    local = local_snapshot(branches=[local_branch("me/done", upstream="origin/me/done")])
    open_pr = pr(9, head="me/done")
    pair = stored(github=snapshot(mine=[open_pr]), local=local)
    refreshes = _Refreshes(pair)
    assert refreshes.deletable() == []  # first: nothing to compare with
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")
    PrRepository(pair[1]).replace(snapshot(merged_recent=[merged]))
    assert refreshes.deletable() == ["me/done: PR merged 1d ago"]
    assert refreshes.deletable() == []  # said once


def test_a_branch_already_deletable_the_first_time_is_not_said() -> None:
    local = local_snapshot(branches=[local_branch("me/gone", upstream="x", gone=True)])
    assert _Refreshes(stored(github=snapshot(), local=local)).deletable() == []


def test_the_plugins_offer_the_kinds_they_make() -> None:
    assert set(GitPlugin.notification_kinds) == {"branch_deletable"}
    assert GitStackPlugin.notification_kinds == events.KINDS


def _stack(*branches: StackBranch) -> list[tuple[str, str]]:
    return [(e.kind, e.text) for e in events.changes(stacks(), stacks(*branches))]


def test_a_branch_needing_a_restack_or_merged_is_said_when_its_status_changes() -> None:
    def branch(status: str) -> StackBranch:
        return StackBranch(name="me/x", parent="main", status=status)

    def between(before: str, after: str) -> list[tuple[str, str]]:
        return [
            (e.kind, e.text) for e in events.changes(stacks(branch(before)), stacks(branch(after)))
        ]

    assert between("up-to-date", "needs-restack") == [("needs_restack", "needs a restack")]
    assert between("needs-restack", "needs-restack") == []
    assert between("up-to-date", "merged") == [("merged", "was merged")]
    assert between("needs-restack", "up-to-date") == []
    assert _stack(branch("needs-restack")) == []  # a branch the earlier snapshot didn't hold
