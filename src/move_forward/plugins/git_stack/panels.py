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
ICED = "iced"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""


def branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=BRANCH, id=f"{repo_dir}{BRANCH_ID_SEPARATOR}{branch}")


def _stack_branch(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=STACK_BRANCH, id=branch_ref(repo_dir, branch).id)


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


def iced_stacks(graph: Graph) -> dict[str, list[str]]:
    """The titles of what's on ice (notes' `iced`), by the checkouts of its branches that git
    or git-stack still has."""
    found: dict[str, list[str]] = {}
    for iced in graph.of_kind(ICED):
        for ref in iced.refs:
            if ref.kind == BRANCH:
                branch = graph.view_one(BRANCH, ref.id, _BranchView)
            elif ref.kind == STACK_BRANCH:
                branch = graph.view_one(STACK_BRANCH, ref.id, _BranchView)
            else:
                continue
            if branch is not None and iced.title not in found.get(branch.repo_dir, []):
                found.setdefault(branch.repo_dir, []).append(iced.title)
    return found


def repo_dirs(graph: Graph) -> list[str]:
    """The checkouts git has branches in, i.e. `[git] repos`."""
    return sorted({b.repo_dir for b in graph.view(BRANCH, _BranchView)})


def entities(snapshot: GitStackSnapshot | None) -> list[Entity]:
    """A `stack_branch` per tracked branch, pointing at git's `branch` of it, which git may
    have deleted."""
    return [
        Entity(
            ref=_stack_branch(repo.dir, b.name),
            title=b.name,
            refs=[branch_ref(repo.dir, b.name)],
            attrs=attrs(
                StackBranchAttrs(
                    repo_dir=repo.dir,
                    name=b.name,
                    stack_parent=b.parent,
                    stack_status=b.status,
                    stack_group=b.group,
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
        self.iced_stacks = iced_stacks(graph)

    def _pr(self, repo_dir: str, branch: str, state: str) -> _PrView | None:
        """My PR from the branch in `state`, e.g. "open" or "merged"."""
        prs = self.my_prs.get((self.github_repos.get(repo_dir, ""), branch), [])
        return next((pr for pr in prs if pr.state == state), None)

    def stacks(self) -> Panel:
        items: list[Item] = []
        actions: list[Action] = []
        for repo in self.snapshot.repos if self.snapshot else []:
            merged = [b.name for b in repo.branches if b.status == "merged"]
            if merged and repo.dir in self.iced_stacks:
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
                items.append(
                    Item(
                        title=f"{b.name}: {b.status}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · on {b.parent}",
                        rank=1,
                        subjects=[_stack_branch(repo.dir, b.name), *([pr.subject] if pr else [])],
                    )
                )
            for b in repo.branches:
                if b.parent is None or b.status == "merged":
                    continue
                if not (self._pr(repo.dir, b.name, "open") or self._pr(repo.dir, b.name, "merged")):
                    items.append(
                        Item(
                            title=f"{b.name}: tracked, no PR",
                            subtitle=f"{repo.dir} · on {b.parent}",
                            rank=0.5,
                            subjects=[_stack_branch(repo.dir, b.name)],
                        )
                    )
        return Panel(
            key="stacks",
            priority=60,  # 6. stacks without PRs
            title="Stacks",
            description="git-stack branches that need a restack, were merged, or have no PR.",
            items=items,
            actions=actions,
        )


def untrack_iced_action(item: Item, graph: Graph) -> Action | None:
    """An Untrack button for an item about what hides its branches, i.e. notes' On ice item,
    when git-stack still tracks some of them. A `stack_branch`'s id is its branch's."""
    if not any(graph.hides(ref.kind) for ref in item.subjects):
        return None
    tracked = [
        b
        for ref in item.subjects
        if ref.kind == BRANCH
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
