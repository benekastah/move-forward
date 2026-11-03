"""What git and git_stack notifications say."""

from datetime import timedelta

from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stacks, stored

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git_stack import events
from move_forward.plugins.git_stack.models import StackBranch
from move_forward.plugins.git_stack.plugin import GitStackPlugin
from move_forward.plugins.github.snapshots import PrRepository


def _deletable(pair: tuple[Config, Database]) -> list[str]:
    config, db = pair
    enabled = registry.enabled(config, db, NOW)
    graph, _ = refresh.stored_graph(db, enabled)
    plugin = next(e for e in enabled if e.name == "git")
    return [e.title for e in GitPlugin().graph_events(plugin.ctx, graph)]


def test_a_branch_whose_pr_merges_is_deletable_once_and_not_the_first_time() -> None:
    local = local_snapshot(branches=[local_branch("me/done", upstream="origin/me/done")])
    open_pr = pr(9, head="me/done")
    pair = stored(github=snapshot(mine=[open_pr]), local=local)
    assert _deletable(pair) == []  # first: keeps what's there
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")
    PrRepository(pair[1]).replace(snapshot(merged_recent=[merged]))
    assert _deletable(pair) == ["me/done: PR merged 1d ago"]
    assert _deletable(pair) == []  # said once


def test_a_branch_already_deletable_the_first_time_is_not_said() -> None:
    local = local_snapshot(branches=[local_branch("me/gone", upstream="x", gone=True)])
    assert _deletable(stored(github=snapshot(), local=local)) == []


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
