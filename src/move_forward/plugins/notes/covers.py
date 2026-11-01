"""What an act of icing hides besides its own items: the PRs from its branches, and what
tracks them (git_stack's `stack_branch`). Nothing in the graph ties a PR to its branch, so
notes finds them, after every refresh and when something is iced."""

from ...domain import Model
from ...graph import Graph, Ref
from .models import Ice

PR = "pr"
REPO = "repo"
BRANCH = "branch"
STACK_BRANCH = "stack_branch"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""


class _RepoView(Model):
    """What notes reads of a `repo` (git's): where it's checked out."""

    id: str
    dir: str


class _PrView(Model):
    """What notes reads of a `pr` (github's): where it's from."""

    id: str
    repo: str
    head: str


def covers(graph: Graph, ices: list[Ice]) -> dict[int, list[Ref]]:
    """By act of icing, the PRs from its branches (in the repo git has them checked out from,
    or in any repo when git doesn't know the checkout) and the entities tracking them."""
    repos = {r.dir: r.id for r in graph.view(REPO, _RepoView)}
    prs = graph.view(PR, _PrView)
    found: dict[int, list[Ref]] = {}
    for ice in ices:
        refs: list[Ref] = []
        for item in ice.items:
            if item.kind != BRANCH:
                continue
            repo_dir, _, name = item.ref_id.partition(BRANCH_ID_SEPARATOR)
            repo = repos.get(repo_dir)
            refs += [
                Ref(kind=PR, id=pr.id) for pr in prs if pr.head == name and repo in (None, pr.repo)
            ]
            branch = Ref(kind=BRANCH, id=item.ref_id)
            refs += [e.ref for e in graph.related(branch, STACK_BRANCH)]
        if refs:
            found[ice.id] = list(dict.fromkeys(refs))
    return found
