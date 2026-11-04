"""Telling that a branch of one of my open PRs moved, from git's `branch` entities: a commit,
a push (it's no longer ahead of its upstream), or a fetch bringing commits pushed from
elsewhere (it's behind). GitHub notifies nobody of their own pushes, so without this github
would see mine only at its next `refresh_seconds`."""

import json

from ...api import Graph, Model, Ref

BRANCH = "branch"
"""git's kind for a local branch."""


class _MyPr(Model):
    """What this reads of github's own `pr` entities."""

    id: str
    state: str | None = None
    repo: str
    number: int
    head: str
    mine: bool


class _Branch(Model):
    """What this reads of git's `branch` entities."""

    name: str
    sha: str | None = None
    ahead: int = 0
    behind: int = 0


def tips(graph: Graph, pr_kind: str, repo_kind: str) -> dict[str, str]:
    """Where the local branch of each open PR of mine is, and how it stands against its
    upstream, by the PR's `owner/name#number`: a value changes when its branch moves."""
    seen: dict[str, str] = {}
    for pr in graph.view(pr_kind, _MyPr):
        if not pr.mine or pr.state != "open":
            continue
        for branch in graph.view_related(Ref(kind=repo_kind, id=pr.repo), BRANCH, _Branch):
            if branch.name == pr.head:
                seen[f"{pr.repo}#{pr.number}"] = json.dumps(
                    [branch.sha, branch.ahead, branch.behind]
                )
    return seen


def moved(before: dict[str, str], now: dict[str, str]) -> set[str]:
    """The PRs whose branch moved between `before` and `now`. One that came or went (a PR
    opened or merged, a branch checked out or deleted) is github's own news, or none."""
    return {pr for pr in before.keys() & now.keys() if before[pr] != now[pr]}
