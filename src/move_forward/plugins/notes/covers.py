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
    or, when git doesn't know the checkout, in any repo git doesn't know one of) and the
    entities tracking them."""
    repos = {r.dir: r.id for r in graph.view(REPO, _RepoView)}
    checked_out = set(repos.values())
    prs = graph.view(PR, _PrView)
    found: dict[int, list[Ref]] = {}
    for ice in ices:
        refs: list[Ref] = []
        for item in ice.items:
            if item.kind != BRANCH:
                continue
            name = item.name
            repo = repos.get(item.repo_dir or "")
            for pr in prs:
                from_here = pr.repo == repo if repo else pr.repo not in checked_out
                if pr.head == name and from_here:
                    refs.append(Ref(kind=PR, id=pr.id))
            branch = Ref(kind=BRANCH, id=item.ref_id)
            refs += [e.ref for e in graph.related(branch, STACK_BRANCH)]
        if refs:
            found[ice.id] = list(dict.fromkeys(refs))
    return found
