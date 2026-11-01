"""Telling that a branch of one of my open PRs moved, from git's `branch` entities: a commit,
a push (it's no longer ahead of its upstream), or a fetch bringing commits pushed from
elsewhere (it's behind). GitHub notifies nobody of their own pushes, so without this github
would see mine only at its next `refresh_seconds`."""

import json

from ...domain import Model
from ...graph import Graph, Ref

BRANCH = "branch"
"""git's kind for a local branch."""


class _MyPr(Model):
    """What this reads of github's own `pr` entities."""

    id: str
    state: str | None = None
    repo: str
    head: str
    mine: bool


class _Branch(Model):
    """What this reads of git's `branch` entities."""

    name: str
    sha: str | None = None
    ahead: int = 0
    behind: int = 0


def tips(graph: Graph, pr_kind: str, repo_kind: str) -> str:
    """Where the local branch of each open PR of mine is, and how it stands against its
    upstream, as a string that changes when any of them moves."""
    seen: list[str] = []
    for pr in graph.view(pr_kind, _MyPr):
        if not pr.mine or pr.state != "open":
            continue
        for branch in graph.view_related(Ref(kind=repo_kind, id=pr.repo), BRANCH, _Branch):
            if branch.name == pr.head:
                seen.append(json.dumps([pr.id, branch.sha, branch.ahead, branch.behind]))
    return json.dumps(sorted(seen))
