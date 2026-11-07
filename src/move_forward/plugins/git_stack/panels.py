"""The git-stack plugin's panel and entities.

Pure functions of the snapshot and the graph; no I/O. A branch's PR is the `pull_request`
the graph relates to its `git.branch` (github relates them).
"""

from pathlib import Path

from ...api import Action, Band, Entity, Graph, Item, Model, Panel, Ref, Signals, attrs
from ...api.ids import branch_id
from ...api.kinds import PULL_REQUEST as PR
from .kinds import StackBranchAttrs
from .models import GitStackSnapshot, StackBranch

BRANCH = "git.branch"
STACK_BRANCH = "git_stack.stack_branch"


def branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=BRANCH, id=branch_id(repo_dir, branch))


def stack_branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=STACK_BRANCH, id=branch_ref(repo_dir, branch).id)


class StackBranchView(Model):
    """A `stack_branch`, git_stack's own: a branch git-stack tracks."""

    id: str
    repo_dir: str
    name: str
    stack_parent: str


class _BranchView(Model):
    """What git_stack reads of a `git.branch`: its checkout."""

    repo_dir: str


class _PrView(Model):
    """What git_stack reads of a `pull_request`."""

    id: str
    url: str | None = None
    state: str | None = None
    repo: str
    head: str
    mine: bool

    @property
    def subject(self) -> Ref:
        return Ref(kind=PR, id=self.id)


def repo_dirs(graph: Graph) -> list[str]:
    """The checkouts git has branches in, i.e. `[git] repos`."""
    return sorted({b.repo_dir for b in graph.view(BRANCH, _BranchView)})


def stack_of(branches: list[StackBranch], branch: str) -> list[str]:
    """The names of what git-stack acts on with `branch`: its stack, and the other stacks in
    its group, since git-stack's `restack`, `sync` and `push` act on all of them. Each stack
    bottom first, the branch's own first of all; just the branch when it isn't tracked."""
    parents = {b.name: b.parent for b in branches if b.parent is not None}
    if branch not in parents:
        return [branch]
    bottom, seen = branch, {branch}
    while parents[bottom] in parents and parents[bottom] not in seen:  # down to the root
        bottom = parents[bottom]
        seen.add(bottom)
    groups = {b.name: b.group for b in branches if b.parent is not None and b.group}
    group = groups.get(bottom)
    bottoms = [bottom] + sorted(
        name
        for name, other in groups.items()
        if group is not None and other == group and name != bottom and parents[name] not in parents
    )
    children: dict[str, list[str]] = {}
    for name, parent in parents.items():
        children.setdefault(parent, []).append(name)
    stack: list[str] = []
    for start in bottoms:
        pending = [start]
        while pending:
            name = pending.pop()
            if name not in stack:
                stack.append(name)
                pending += reversed(children.get(name, []))
    return stack


def entities(snapshot: GitStackSnapshot | None) -> list[Entity]:
    """A `git_stack.stack_branch` per tracked branch, pointing at its `git.branch`, which git
    may have deleted, with the stack and group git-stack acts on with it."""
    return [
        Entity(
            ref=stack_branch_ref(repo.dir, b.name),
            title=b.name,
            refs=[branch_ref(repo.dir, b.name)],
            attrs=attrs(
                StackBranchAttrs(
                    repo_dir=repo.dir,
                    name=b.name,
                    stack_parent=b.parent,
                    stack_status=b.status,
                    stack_group=b.group,
                    stack=[branch_id(repo.dir, n) for n in stack_of(repo.branches, b.name)],
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
        self.graph = graph

    def _pr(self, repo_dir: str, branch: str, state: str) -> _PrView | None:
        """My PR from the branch in `state`, e.g. "open" or "merged": one the graph relates
        to it (github does)."""
        prs = self.graph.view_related(branch_ref(repo_dir, branch), PR, _PrView)
        return next((pr for pr in prs if pr.mine and pr.state == state), None)

    def stacks(self) -> Panel:
        items: list[Item] = []
        actions: list[Action] = []
        for repo in self.snapshot.repos if self.snapshot else []:
            merged = [b.name for b in repo.branches if b.status == "merged"]
            clean = Action(
                name="git_stack/stack_clean",
                label="Clean",
                fields={"repo": repo.dir},
                confirm=f"Run `git stack clean -d local` in {repo.dir}? It fetches,"
                " fast-forwards each stack's root (e.g. main), untracks the branches"
                " that merged (iced stacks' too) and deletes them locally. With"
                " uncommitted changes there, it"
                " refuses if the checked-out branch is a stack's root or one it would"
                " delete.",
            )
            if merged:
                # The count is from the last `info --offline`; clean fetches and asks GitHub, so
                # it may find more or fewer.
                label = f"Clean about {len(merged)} merged in {Path(repo.dir).name}"
                actions.append(clean.model_copy(update={"label": label}))
            for b in repo.branches:
                if b.status in (None, "up-to-date"):
                    continue
                pr = self._pr(repo.dir, b.name, "open")
                is_merged = b.status == "merged"
                items.append(
                    Item(
                        title=f"{b.name}: {b.status}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · on {b.parent}",
                        rank=1,
                        actions=[clean] if is_merged else [],
                        signals=Signals(verb="Clean up", task="stack_clean", effort="minutes")
                        if is_merged
                        else None,
                        subjects=[
                            stack_branch_ref(repo.dir, b.name),
                            branch_ref(repo.dir, b.name),
                            *([pr.subject] if pr else []),
                        ],
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
                            subjects=[
                                stack_branch_ref(repo.dir, b.name),
                                branch_ref(repo.dir, b.name),
                            ],
                        )
                    )
        return Panel(
            key="stacks",
            priority=Band.STACKS_NO_PR,
            title="Stacks",
            description="git-stack branches that need a restack, were merged, or have no PR.",
            items=items,
            actions=actions,
        )


def untrack_iced_action(item: Item, graph: Graph) -> Action | None:
    """An Untrack button for an item about what hides its branches, i.e. notes' On ice item,
    when git-stack still tracks some of them. A `git_stack.stack_branch`'s id is its branch's."""
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
