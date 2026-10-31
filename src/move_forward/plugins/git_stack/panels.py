"""The git-stack plugin's panel and entities.

Pure functions of the snapshot and the graph; no I/O. A branch's PR comes from the graph's
`pr` entities, matched by their `repo` and `head`, and a checkout's GitHub repo from git's
`repo` entities.
"""

from pathlib import Path

from ...domain import GitStackSnapshot
from ...graph import Entity, Graph, Ref
from ...page import Action, Item, Panel

BRANCH = "branch"
PR = "pr"
REPO = "repo"
ICED_BRANCH = "iced_branch"
BRANCH_ID_SEPARATOR = "\t"
"""Joins a repo dir and a branch name in a `branch` entity's id."""


def branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=BRANCH, id=f"{repo_dir}{BRANCH_ID_SEPARATOR}{branch}")


def repo_dirs(graph: Graph) -> list[str]:
    """The checkouts git has branches in, i.e. `[git] repos`."""
    return sorted(
        {d for b in graph.of_kind(BRANCH) if isinstance(d := b.attrs.get("repo_dir"), str)}
    )


def entities(snapshot: GitStackSnapshot | None) -> list[Entity]:
    """`stack_parent` and `stack_status` on the `branch` of each tracked branch."""
    return [
        Entity(
            ref=branch_ref(repo.dir, b.name),
            title=b.name,
            attrs={"stack_parent": b.parent, "stack_status": b.status},
        )
        for repo in (snapshot.repos if snapshot else [])
        for b in repo.branches
        if b.parent is not None
    ]


class Builder:
    def __init__(self, snapshot: GitStackSnapshot | None, graph: Graph):
        self.snapshot = snapshot
        self.github_repos = {
            str(r.attrs.get("dir")): r.ref.id for r in graph.of_kind(REPO) if r.attrs.get("dir")
        }
        self.my_prs: dict[tuple[str, str], list[Entity]] = {}
        for pr in graph.of_kind(PR):
            if pr.attrs.get("mine"):
                key = (str(pr.attrs.get("repo")), str(pr.attrs.get("head")))
                self.my_prs.setdefault(key, []).append(pr)
        iced = graph.of_kind(ICED_BRANCH)
        self.iced_branches = {e.ref.id for e in iced}
        self.iced_repo_dirs = {
            str(Path(str(e.attrs.get("repo_dir", ""))).expanduser()) for e in iced
        }

    def _pr(self, repo_dir: str, branch: str, state: str) -> Entity | None:
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
                badges = ["iced"] if b.name in self.iced_branches else []
                items.append(
                    Item(
                        title=f"{b.name}: {b.status}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · on {b.parent}",
                        badges=badges,
                        rank=0 if badges else 1,
                        subject=pr.ref if pr else None,
                    )
                )
            for b in repo.branches:
                if b.parent is None or b.status == "merged" or b.name in self.iced_branches:
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
