"""What git and git_stack notifications say."""

from datetime import timedelta
from typing import cast

from conftest import (
    NOW,
    events_between,
    local_branch,
    local_snapshot,
    pr,
    snapshot,
    stacks,
    stored,
)

from move_forward import refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.notifications import Event
from move_forward.page import anchor
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git_stack import events
from move_forward.plugins.git_stack.models import GitStackSnapshot, StackBranch
from move_forward.plugins.git_stack.panels import stack_branch_ref
from move_forward.plugins.git_stack.plugin import GitStackPlugin
from move_forward.plugins.github.snapshots import PrRepository


class _Refreshes:
    """What core finds of git's events over what's stored, as successive refreshes do:
    each against what the one before saw (the first against nothing)."""

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
        self.previous = (graph, now)
        events = refresh.events_of(git, before, now, before_graph, graph)
        return [e.title for e in events if e.kind == "branch_deletable"]


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


def test_events_between_two_refreshes_tells_what_newly_shows() -> None:
    local = local_snapshot(branches=[local_branch("me/done", upstream="origin/me/done")])
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")
    said = events_between(
        "git",
        {"github": snapshot(mine=[pr(9, head="me/done")]), "local": local},
        {"github": snapshot(merged_recent=[merged]), "local": local},
    )
    assert [(e.kind, e.title) for e in said] == [("branch_deletable", "me/done: PR merged 1d ago")]


def test_the_plugins_offer_the_kinds_they_make() -> None:
    assert set(GitPlugin.notification_kinds) == {"branch_deletable"}
    assert GitStackPlugin.notification_kinds == events.KINDS


def _changes(before: GitStackSnapshot, after: GitStackSnapshot) -> list[Event]:
    return events_between("git_stack", {"stacks": before}, {"stacks": after})


def _stack(*branches: StackBranch) -> list[tuple[str, str]]:
    return [(e.kind, e.text) for e in _changes(stacks(), stacks(*branches))]


def _branch(status: str) -> StackBranch:
    return StackBranch(name="me/x", parent="main", status=status)


def test_a_branch_needing_a_restack_or_merged_is_said_when_its_status_changes() -> None:
    def between(before: str, after: str) -> list[tuple[str, str]]:
        return [(e.kind, e.text) for e in _changes(stacks(_branch(before)), stacks(_branch(after)))]

    assert between("up-to-date", "needs-restack") == [("needs_restack", "needs a restack")]
    assert between("needs-restack", "needs-restack") == []
    assert between("up-to-date", "merged") == [("merged", "was merged")]
    assert between("needs-restack", "up-to-date") == []
    assert _stack(_branch("needs-restack")) == []  # a branch the earlier snapshot didn't hold


def test_a_stack_branchs_event_is_about_the_branch_and_opens_at_it() -> None:
    [event] = _changes(stacks(_branch("up-to-date")), stacks(_branch("needs-restack")))
    ref = stack_branch_ref("/src/widgets", "me/x")
    assert (event.thing, event.title, event.anchor) == (
        f"stack:{ref.id}",
        "me/x in /src/widgets",
        anchor(ref.kind, ref.id),
    )
