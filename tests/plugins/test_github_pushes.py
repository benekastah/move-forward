"""github telling that a branch of one of my PRs moved, from git's branches in the graph."""

import subprocess

import pytest
from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stored

from move_forward.change_checks import ChangeChecks
from move_forward.plugins.git.repository import LocalRepository
from move_forward.plugins.github import notifications
from move_forward.refresh import stored_graph
from move_forward.registry import Enabled, enabled
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

    checks = ChangeChecks()  # as the server keeps what the checks said, graphs and all

    def github() -> Enabled:
        return next(e for e in enabled(config, db, NOW) if e.name == "github")

    def changed_after(*branches: object) -> bool:
        if branches:
            LocalRepository(db).replace(local_snapshot(branches=list(branches)))
        graph = stored_graph(db, enabled(config, db, NOW))[0]
        return checks.check(github(), graph) is not None

    def collected() -> None:
        checks.stored(github(), checks.take(github(), None))

    assert not changed_after()  # the first look notes where things stand
    assert not changed_after()
    assert changed_after(local_branch(mine.head, sha="a", ahead=0))  # pushed
    assert checks.take(github(), None).subjects == {"acme/widgets#1"}  # for collect
    # Pending until a collect fetches it, but asked for once: nothing new moved since.
    assert not changed_after()
    collected()
    assert changed_after(local_branch(mine.head, sha="b", ahead=1))  # committed
    collected()
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

    checks = ChangeChecks()

    def github() -> Enabled:
        return next(e for e in enabled(config, db, NOW) if e.name == "github")

    def changed() -> bool:
        return checks.check(github(), stored_graph(db, enabled(config, db, NOW))[0]) is not None

    assert not changed()  # the first look notes where things stand
    LocalRepository(db).replace(local_snapshot(branches=[local_branch(mine.head, ahead=0)]))
    assert changed()
    assert checks.take(github(), None).subjects == {"acme/widgets#1"}
