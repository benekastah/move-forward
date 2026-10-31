"""The git plugin's panels: local work, git-stack stacks, and branches to clean up.

Pure functions of the snapshot and the graph; no I/O. A branch's PR comes from the graph's
`pr` entities, matched by their `repo` and `head`.
"""

from datetime import datetime
from pathlib import Path

from ...domain import LocalSnapshot
from ...graph import Entity, Graph, Ref
from ...page import Action, Item, Panel, age
from .config import GitConfig

TARGET_SEPARATOR = "\t"
"""Joins a repo dir and a branch in a bulk action's `targets` field, one per line."""
PR = "pr"
ICED_BRANCH = "iced_branch"


def action(name: str, label: str, fields: dict[str, str], confirm: str | None = None) -> Action:
    return Action(name=f"git/{name}", label=label, fields=fields, confirm=confirm)


def _ice_action(repo_dir: str, branch: str) -> Action:
    return Action(name="notes/ice", label="Put on ice", fields={"repo": repo_dir, "branch": branch})


class Builder:
    def __init__(
        self, config: GitConfig, now: datetime, snapshot: LocalSnapshot | None, graph: Graph
    ):
        self.config = config
        self.now = now
        self.local = snapshot
        self.prs_by_branch: dict[tuple[str, str], list[Entity]] = {}
        for pr in graph.of_kind(PR):
            if pr.attrs.get("mine"):
                key = (str(pr.attrs.get("repo")), str(pr.attrs.get("head")))
                self.prs_by_branch.setdefault(key, []).append(pr)
        iced = graph.of_kind(ICED_BRANCH)
        self.iced_branches = {e.ref.id for e in iced}
        self.iced_repo_dirs = {
            str(Path(str(e.attrs.get("repo_dir", ""))).expanduser()) for e in iced
        }

    def _pr(self, repo: str | None, branch: str, state: str) -> Entity | None:
        """My PR from the branch in `state`, e.g. "open" or "merged"."""
        prs = self.prs_by_branch.get((repo or "", branch), [])
        return next((pr for pr in prs if pr.state == state), None)

    def _is_my_branch(self, repo: str | None, name: str) -> bool:
        """Mine by prefix (when prefixes are configured) or by having a PR of mine."""
        prefixes = self.config.my_branch_prefixes
        if not prefixes or any(name.startswith(p) for p in prefixes):
            return True
        return (repo or "", name) in self.prs_by_branch

    def local_work(self) -> Panel:
        items: list[Item] = []
        for repo in self.local.repos if self.local else []:
            for wt in repo.worktrees:
                if wt.modified or wt.untracked:
                    pr = self._pr(repo.github_repo, wt.branch or "", "open")
                    actions: list[Action] = []
                    if wt.modified:
                        actions.append(
                            action(
                                "wip_commit", "WIP commit", {"repo": repo.dir, "worktree": wt.path}
                            )
                        )
                    if wt.branch and wt.branch not in self.iced_branches:
                        actions.append(_ice_action(repo.dir, wt.branch))
                    items.append(
                        Item(
                            title=f"{wt.branch or 'detached HEAD'}: {wt.modified} modified,"
                            f" {wt.untracked} untracked",
                            url=pr.url if pr else None,
                            subtitle=wt.path,
                            badges=["uncommitted"],
                            rank=2,
                            actions=actions,
                            subject=pr.ref if pr else None,
                        )
                    )
            for branch in repo.branches:
                if branch.name == repo.default_branch or not self._is_my_branch(
                    repo.github_repo, branch.name
                ):
                    continue
                reason = None
                if branch.ahead:
                    reason = f"{branch.ahead} unpushed commits"
                elif branch.upstream is None and branch.ahead_of_default:
                    reason = f"{branch.ahead_of_default} commits, never pushed"
                if not reason:
                    continue
                pr = self._pr(repo.github_repo, branch.name, "open")
                iced = branch.name in self.iced_branches
                actions = []
                if not branch.behind:
                    actions.append(
                        action("push_branch", "Push", {"repo": repo.dir, "branch": branch.name})
                    )
                if not iced:
                    actions.append(_ice_action(repo.dir, branch.name))
                items.append(
                    Item(
                        title=f"{branch.name}: {reason}",
                        url=pr.url if pr else None,
                        subtitle=f"{repo.dir} · last commit {age(self.now, branch.committed_at)} ago",
                        badges=(["iced"] if iced else []) + (["has PR"] if pr else ["no PR"]),
                        rank=0 if iced else 1,
                        actions=actions,
                        subject=pr.ref if pr else None,
                    )
                )
        return Panel(
            key="local_work",
            title="Local work",
            description="Uncommitted changes in worktrees, and branches with unpushed commits.",
            items=items,
        )

    def uses_git_stack(self) -> bool:
        """Whether any repo has tracked stacks; without them the Stacks panel is left out."""
        return bool(self.local) and any(repo.stack for repo in self.local.repos)

    def stacks(self) -> Panel:
        items: list[Item] = []
        actions: list[Action] = []
        for repo in self.local.repos if self.local else []:
            merged = [b.name for b in repo.stack if b.status == "merged"]
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
                    action(
                        "stack_clean",
                        f"Clean {len(merged)} merged in {Path(repo.dir).name}",
                        {"repo": repo.dir},
                        confirm=f"Run `git stack clean -d local` in {repo.dir}? It fetches,"
                        " fast-forwards main, untracks merged branches and deletes them locally.",
                    )
                )
            for b in repo.stack:
                if b.status in (None, "up-to-date"):
                    continue
                pr = self._pr(repo.github_repo, b.name, "open")
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
            for b in repo.stack:
                if b.parent is None or b.status == "merged" or b.name in self.iced_branches:
                    continue
                if not (
                    self._pr(repo.github_repo, b.name, "open")
                    or self._pr(repo.github_repo, b.name, "merged")
                ):
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

    def cleanup(self) -> Panel:
        items: list[Item] = []
        targets: list[str] = []
        for repo in self.local.repos if self.local else []:
            for b in repo.branches:
                merged = self._pr(repo.github_repo, b.name, "merged")
                if merged:
                    merged_at = merged.attrs.get("merged_at")
                    when = datetime.fromisoformat(merged_at) if isinstance(merged_at, str) else None
                    title = f"{b.name}: PR merged {age(self.now, when)} ago"
                    subtitle = repo.dir
                elif b.gone and not b.ahead and self._is_my_branch(repo.github_repo, b.name):
                    title = f"{b.name}: upstream gone"
                    subtitle = f"{repo.dir} · last commit {age(self.now, b.committed_at)} ago"
                else:
                    continue
                targets.append(f"{repo.dir}{TARGET_SEPARATOR}{b.name}")
                items.append(
                    Item(
                        title=title,
                        url=merged.url if merged else None,
                        subtitle=subtitle,
                        badges=["git branch -D"],
                        actions=[
                            action(
                                "delete_branch",
                                "Delete",
                                {"repo": repo.dir, "branch": b.name},
                                confirm=f"Delete local branch {b.name}?",
                            )
                        ],
                        subject=merged.ref if merged else None,
                    )
                )
        actions = (
            [
                action(
                    "delete_branches",
                    f"Delete all {len(targets)}",
                    {"targets": "\n".join(targets)},
                    confirm=f"Delete all {len(targets)} local branches listed here?",
                )
            ]
            if targets
            else []
        )
        return Panel(
            key="cleanup",
            title="Cleanup",
            description="Local branches whose PR merged or whose remote branch is gone.",
            items=items,
            actions=actions,
        )

    def panels(self) -> list[Panel]:
        return [
            self.local_work(),
            *([self.stacks()] if self.uses_git_stack() else []),
            self.cleanup(),
        ]


def branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind="branch", id=f"{repo_dir}{TARGET_SEPARATOR}{branch}")
