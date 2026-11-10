"""github telling that a branch of one of my PRs moved, from git's branches in the graph."""

import subprocess

import pytest
from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stored

from move_forward.graph import Graph
from move_forward.plugin import ChangedRequest
from move_forward.plugins.git.repository import LocalRepository
from move_forward.plugins.github import notifications
from move_forward.plugins.github.plugin import CHANGED, GitHubPlugin
from move_forward.refresh import stored_graph
from move_forward.registry import enabled
from move_forward.repositories import KvRepository
from move_forward.shell import CommandError


def test_a_commit_or_push_on_my_prs_branch_says_github_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def not_modified(host: str, if_modified_since: str | None) -> None:
        return None

    monkeypatch.setattr(notifications, "fetch", not_modified)
    mine = pr(1)  # its head is me/APP-1-branch
    config, db = stored(
        github=snapshot(mine=[mine]),
        local=local_snapshot(branches=[local_branch(mine.head, sha="a", ahead=1)]),
    )

    checked: list[Graph] = []  # as core keeps them, for the next check

    def changed_after(*branches: object) -> bool:
        if branches:
            LocalRepository(db).replace(local_snapshot(branches=list(branches)))
        plugins = enabled(config, db, NOW)
        github = next(e for e in plugins if e.name == "github")
        graph = stored_graph(db, plugins)[0]
        previous = checked[-1] if checked else None
        checked.append(graph)
        return bool(GitHubPlugin().changed(ChangedRequest(github.ctx, graph, previous)))

    assert not changed_after()  # the first look notes where things stand
    assert not changed_after()
    assert changed_after(local_branch(mine.head, sha="a", ahead=0))  # pushed
    kv = KvRepository(db, "github")
    assert kv.get(CHANGED) == '["acme/widgets#1"]'  # for collect
    assert changed_after()  # still, until collect fetches it
    kv.delete(CHANGED)  # as collect does
    assert changed_after(local_branch(mine.head, sha="b", ahead=1))  # committed
    kv.delete(CHANGED)
    assert not changed_after(  # someone else's branch isn't my PR's
        local_branch(mine.head, sha="b", ahead=1), local_branch("theirs", sha="c")
    )


@pytest.mark.parametrize(
    "failure",
    [
        CommandError("gh api: HTTP 403: Resource not accessible by personal access token"),
        subprocess.TimeoutExpired(["gh", "api"], 120),
    ],
)
def test_a_push_counts_when_the_notifications_cant_be_read(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def refused(host: str, if_modified_since: str | None) -> None:
        raise failure

    monkeypatch.setattr(notifications, "fetch", refused)
    mine = pr(1)
    config, db = stored(
        github=snapshot(mine=[mine]),
        local=local_snapshot(branches=[local_branch(mine.head, sha="a", ahead=1)]),
    )

    checked: list[Graph] = []

    def changed() -> bool:
        plugins = enabled(config, db, NOW)
        github = next(e for e in plugins if e.name == "github")
        graph = stored_graph(db, plugins)[0]
        previous = checked[-1] if checked else None
        checked.append(graph)
        return bool(GitHubPlugin().changed(ChangedRequest(github.ctx, graph, previous)))

    assert not changed()  # the first look notes where things stand
    LocalRepository(db).replace(local_snapshot(branches=[local_branch(mine.head, ahead=0)]))
    assert changed()
    assert KvRepository(db, "github").get(CHANGED) == '["acme/widgets#1"]'
