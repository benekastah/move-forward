"""The git-stack plugin's panel and entities.

Pure functions of the snapshot and the graph; no I/O. A branch's PR comes from the graph's
`pr` entities, matched by their `repo` and `head`, and a checkout's GitHub repo from git's
`repo` entities.
"""

from pathlib import Path

from ...domain import Model
from ...graph import Entity, Graph, Ref, attrs
from ...page import Action, Item, Panel
from .kinds import StackBranchAttrs
from .models import GitStackSnapshot

BRANCH = "branch"
STACK_BRANCH = "stack_branch"
PR = "pr"
REPO = "repo"
ICED_BRANCH = "iced_branch"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""


def branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=BRANCH, id=f"{repo_dir}{BRANCH_ID_SEPARATOR}{branch}")


class StackBranchView(Model):
    """A `stack_branch`, git_stack's own: a branch git-stack tracks."""

    id: str
    repo_dir: str
    name: str
    stack_parent: str


class _BranchView(Model):
    """What git_stack reads of a `branch` (git's): its checkout."""

    repo_dir: str


class _RepoView(Model):
    """What git_stack reads of a `repo` (git's): GitHub's name for a checkout."""

    id: str
    dir: str


class _PrView(Model):
    """What git_stack reads of a `pr` (github's)."""

    id: str
    url: str | None = None
    state: str | None = None
    repo: str
    head: str
    mine: bool

    @property
    def subject(self) -> Ref:
        return Ref(kind=PR, id=self.id)


class IcedView(Model):
    """What git_stack reads of an `iced_branch` (notes')."""

    id: str
    repo_dir: str
    stack: str


def repo_dirs(graph: Graph) -> list[str]:
    """The checkouts git has branches in, i.e. `[git] repos`."""
    return sorted({b.repo_dir for b in graph.view(BRANCH, _BranchView)})


def entities(snapshot: GitStackSnapshot | None) -> list[Entity]:
    """A `stack_branch` per tracked branch, pointing at git's `branch` of it, which git may
    have deleted."""
    return [
        Entity(
            ref=Ref(kind=STACK_BRANCH, id=branch_ref(repo.dir, b.name).id),
            title=b.name,
            refs=[branch_ref(repo.dir, b.name)],
            attrs=attrs(
                StackBranchAttrs(
                    repo_dir=repo.dir, name=b.name, stack_parent=b.parent, stack_status=b.status
                )
            ),
        )
        for repo in (snapshot.repos if snapshot else [])
        for b in repo.branches
        if b.parent is not None
    ]


class Builder:
    def __init__(self, snapshot: GitStackSnapshot | None, graph: Graph):
        self.snapshot = snapshot
        self.github_repos = {r.dir: r.id for r in graph.view(REPO, _RepoView)}
        self.my_prs: dict[tuple[str, str], list[_PrView]] = {}
        for pr in graph.view(PR, _PrView):
            if pr.mine:
                self.my_prs.setdefault((pr.repo, pr.head), []).append(pr)
        iced = graph.view(ICED_BRANCH, IcedView)
        self.iced_branches = {e.id for e in iced}
        self.iced_repo_dirs = {e.repo_dir for e in iced}

    def _pr(self, repo_dir: str, branch: str, state: str) -> _PrView | None:
        """My PR from the branch in `state`, e.g. "open" or "merged"."""
        prs = self.my_prs.get((self.github_repos.get(repo_dir, ""), branch), [])
        return next((pr for pr in prs if pr.state == state), None)

    def stacks(self) -> Panel:
        items: list[Item] = []
        actions: list[Action] = []
        for repo in self.snapshot.repos if self.snapshot else []:
            merged = [b.name for b in repo.branches if b.status == "merged"]
            if merged and repo.dir in self.iced_repo_dirs:
                items.append(
                    Item(
                        title=f"{len(merged)} merged stack branches not cleaned",
                        subtitle=repo.dir,
                        details=["this repo has iced stacks, so `git stack clean` is left to you"],
                        rank=-1,
                    )
                )
            elif merged:
                actions.append(
                    Action(
                        name="git_stack/stack_clean",
                        label=f"Clean {len(merged)} merged in {Path(repo.dir).name}",
                        fields={"repo": repo.dir},
                        confirm=f"Run `git stack clean -d local` in {repo.dir}? It fetches,"
                        " fast-forwards main, untracks merged branches and deletes them locally.",
                    )
                )
            for b in repo.branches:
                if b.status in (None, "up-to-date"):
                    continue
                pr = self._pr(repo.dir, b.name, "open")
                badges = ["iced"] if branch_ref(repo.dir, b.name).id in self.iced_branches else []
                items.append(
                    Item(
                        title=f"{b.name}: {b.status}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · on {b.parent}",
                        badges=badges,
                        rank=0 if badges else 1,
                        subjects=[pr.subject] if pr else [],
                    )
                )
            for b in repo.branches:
                if (
                    b.parent is None
                    or b.status == "merged"
                    or branch_ref(repo.dir, b.name).id in self.iced_branches
                ):
                    continue
                if not (self._pr(repo.dir, b.name, "open") or self._pr(repo.dir, b.name, "merged")):
                    items.append(
                        Item(
                            title=f"{b.name}: tracked, no PR",
                            subtitle=f"{repo.dir} · on {b.parent}",
                            rank=0.5,
                        )
                    )
        return Panel(
            key="stacks",
            title="Stacks",
            description="git-stack branches that need a restack, were merged, or have no PR.",
            items=items,
            actions=actions,
        )


def untrack_iced_action(item: Item, graph: Graph) -> Action | None:
    """An Untrack button for an item about iced branches, i.e. notes' On ice item, when
    git-stack still tracks some of them. An `iced_branch`'s id is its branch's."""
    tracked = [
        b
        for ref in item.subjects
        if ref.kind == ICED_BRANCH
        and (b := graph.view_one(STACK_BRANCH, ref.id, StackBranchView)) is not None
    ]
    if not tracked:
        return None
    return Action(
        name="git_stack/untrack",
        label="Untrack",
        fields={"targets": "\n".join(b.id for b in tracked)},
        confirm="Stop git-stack tracking "
        + ", ".join(b.name for b in tracked)
        + "? The branches themselves are left alone.",
    )
