"""github relates each pull request to the local branch it comes from, and git, git_stack,
notes and github's own push tracking read that one relation."""

import pytest
from conftest import NOW, local_branch, local_snapshot, pr, snapshot, stored

from move_forward import refresh
from move_forward.graph import Graph, Ref
from move_forward.plugins.git.models import Branch, LocalRepo, LocalSnapshot
from move_forward.registry import enabled

WIDGETS = "/src/widgets"
PR_KIND = "pull_request"
BRANCH = "git.branch"


def _graph(github: object, local: LocalSnapshot) -> Graph:
    config, db = stored(github=github, local=local)
    graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
    return graph


def _from(graph: Graph, branch: str, repo_dir: str = WIDGETS) -> list[str]:
    return [e.ref.id for e in graph.related(Ref(kind=BRANCH, id=f"{repo_dir}\t{branch}"), PR_KIND)]


@pytest.mark.parametrize(
    ("branch", "mine", "related"),
    [
        (local_branch("me/APP-1-branch"), True, True),  # its head's name
        (local_branch("renamed", upstream="origin/me/APP-1-branch"), True, True),  # upstream's
        (local_branch("other", sha="abc"), True, True),  # mine, at its head's commit
        (local_branch("other", sha="abc"), False, False),  # someone else's: not by commit
        (local_branch("other", sha="def"), True, False),
    ],
)
def test_a_pr_relates_to_the_branch_it_comes_from(
    branch: Branch, mine: bool, related: bool
) -> None:
    the_pr = pr(1, head_oid="abc")
    github = snapshot(mine=[the_pr]) if mine else snapshot(direct_requests=[the_pr])
    graph = _graph(github, local_snapshot(branches=[branch]))
    assert _from(graph, branch.name) == ([the_pr.url] if related else [])


def test_a_branch_of_its_name_in_another_repos_checkout_isnt_its() -> None:
    other = LocalRepo.model_validate(
        {
            "dir": "/src/other",
            "github_repo": "acme/other",
            "default_branch": "main",
            "worktrees": [],
            "branches": [local_branch("me/APP-1-branch")],
        }
    )
    widgets = local_snapshot(branches=[local_branch("me/APP-1-branch")]).repos[0]
    graph = _graph(snapshot(mine=[pr(1)]), LocalSnapshot(repos=[widgets, other]))
    assert _from(graph, "me/APP-1-branch") == [pr(1).url]
    assert _from(graph, "me/APP-1-branch", "/src/other") == []


def test_the_branch_sees_its_pr_and_the_pr_its_branch() -> None:
    graph = _graph(
        snapshot(mine=[pr(1)]), local_snapshot(branches=[local_branch("me/APP-1-branch")])
    )
    branches = graph.related(Ref(kind=PR_KIND, id=pr(1).url), BRANCH)
    assert [b.ref.id for b in branches] == [f"{WIDGETS}\tme/APP-1-branch"]
