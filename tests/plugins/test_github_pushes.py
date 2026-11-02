"""github telling that a branch of one of my PRs moved, from git's branches in the graph."""

import pytest
from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stored

from move_forward.plugins.git.repository import LocalRepository
from move_forward.plugins.github import notifications
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.refresh import stored_graph
from move_forward.registry import enabled


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

    def changed_after(*branches: object) -> bool:
        if branches:
            LocalRepository(db).replace(local_snapshot(branches=list(branches)))
        plugins = enabled(config, db, NOW)
        github = next(e for e in plugins if e.name == "github")
        return GitHubPlugin().changed(github.ctx, stored_graph(db, plugins)[0])

    assert not changed_after()  # the first look notes where things stand
    assert not changed_after()
    assert changed_after(local_branch(mine.head, sha="a", ahead=0))  # pushed
    assert changed_after(local_branch(mine.head, sha="b", ahead=1))  # committed
    assert not changed_after(  # someone else's branch isn't my PR's
        local_branch(mine.head, sha="b", ahead=1), local_branch("theirs", sha="c")
    )
