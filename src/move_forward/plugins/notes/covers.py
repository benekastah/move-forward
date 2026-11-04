"""What an act of icing hides besides its own items: the PRs from its branches, what tracks
them (git_stack's `stack_branch`), and what an iced work entry links. Nothing in the graph
ties a PR to its branch, and an entry's links change, so notes finds them after every
refresh and when something is iced."""

from ...api import Graph, Model, Ref
from ...api.kinds import PULL_REQUEST as PR
from .models import Ice

REPO = "git.repo"
BRANCH = "git.branch"
STACK_BRANCH = "git_stack.stack_branch"
ENTRY = "notes.entry"


class _RepoView(Model):
    """What notes reads of a `git.repo`: where it's checked out."""

    id: str
    dir: str


class _PrView(Model):
    """What notes reads of a `pull_request`: where it's from."""

    id: str
    repo: str
    head: str


def covers(graph: Graph, ices: list[Ice]) -> dict[int, list[Ref]]:
    """By act of icing, the PRs from its branches (those the graph relates to a branch git
    has; for one it no longer has, those of its name in the repo of its checkout, or, when
    git doesn't know the checkout, in any repo git doesn't know one of), the entities
    tracking them, and what its work entries point at."""
    repos = {r.dir: r.id for r in graph.view(REPO, _RepoView)}
    checked_out = set(repos.values())
    prs = graph.view(PR, _PrView)
    found: dict[int, list[Ref]] = {}
    for ice in ices:
        refs: list[Ref] = []
        for item in ice.items:
            if item.kind == ENTRY and (entry := graph.get(ENTRY, item.ref_id)) is not None:
                refs += entry.refs  # so the PRs mentioning its tickets are hidden too
            if item.kind != BRANCH:
                continue
            branch = Ref(kind=BRANCH, id=item.ref_id)
            if graph.get(BRANCH, item.ref_id) is not None:
                refs += [e.ref for e in graph.related(branch, PR)]  # github relates them
            else:  # git has deleted it, or doesn't know its checkout: by its name
                repo = repos.get(item.repo_dir or "")
                for pr in prs:
                    from_here = pr.repo == repo if repo else pr.repo not in checked_out
                    if pr.head == item.name and from_here:
                        refs.append(Ref(kind=PR, id=pr.id))
            refs += [e.ref for e in graph.related(branch, STACK_BRANCH)]
        if refs:
            found[ice.id] = list(dict.fromkeys(refs))
    return found
