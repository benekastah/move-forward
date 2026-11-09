"""Which local branch each pull request comes from: github's relations (`Plugin.relate`),
from git's `git.repo` and `git.branch` entities."""

from ...api import Graph, Model, Ref
from ...api.kinds import PULL_REQUEST as PR

REPO = "git.repo"
BRANCH = "git.branch"


class _Repo(Model):
    """What github reads of a `git.repo`: its GitHub name, and its checkout."""

    id: str
    dir: str


class _Branch(Model):
    """What github reads of a `git.branch`."""

    id: str
    repo_dir: str
    name: str
    sha: str | None = None
    upstream: str | None = None


class _Pr(Model):
    """What github reads back of its own pull requests."""

    id: str
    repo: str
    head: str
    head_oid: str | None = None
    mine: bool


def relations(graph: Graph, kind: str) -> list[tuple[Ref, Ref]]:
    """Each pull request (of `kind`, github's own) related to the local branches it comes
    from, in the checkout of its repo: the branch of its head's name; else, for a branch
    renamed since it was pushed, the one whose upstream has that name; else, for one of
    mine, a branch at the commit its head is at. Repos match whatever case the remote names
    one in, as GitHub's names ignore case."""
    github_repo = {r.dir: r.id.lower() for r in graph.view(REPO, _Repo)}
    prs: dict[str, list[_Pr]] = {}
    for pr in graph.view(kind, _Pr):
        prs.setdefault(pr.repo.lower(), []).append(pr)
    found: list[tuple[Ref, Ref]] = []
    for branch in graph.view(BRANCH, _Branch):
        here = prs.get(github_repo.get(branch.repo_dir, ""), [])
        upstream = (
            branch.upstream.split("/", 1)[1] if branch.upstream and "/" in branch.upstream else None
        )
        from_it = (
            [pr for pr in here if pr.head == branch.name]
            or [pr for pr in here if upstream is not None and pr.head == upstream]
            or [pr for pr in here if pr.mine and branch.sha and pr.head_oid == branch.sha]
        )
        target = Ref(kind=BRANCH, id=branch.id)
        found += [(Ref(kind=PR, id=pr.id), target) for pr in from_it]
    return found
