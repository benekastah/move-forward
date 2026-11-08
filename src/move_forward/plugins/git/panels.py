"""The git plugin's panels: local work and branches to clean up.

Pure functions of the snapshot and the graph; no I/O. A branch's PRs are the
`pull_request`s the graph relates to its `git.branch` (github relates them, by the branch's
name, its upstream's, or its commit).
"""

from datetime import datetime

from ...api import Action, Badge, Band, Graph, Item, Model, Panel, Ref, Signals, age
from ...api.ids import branch_id, branch_parts
from ...api.kinds import PULL_REQUEST as PR
from .config import GitConfig
from .models import Branch, LocalRepo, LocalSnapshot

BRANCH = "git.branch"
STACK_BRANCH = "git_stack.stack_branch"
"""git_stack's kind for a branch git-stack tracks."""
ICED = "notes.iced"


def action(
    name: str,
    label: str,
    fields: dict[str, str],
    confirm: str | None = None,
    group: str | None = None,
    icon: str | None = None,
) -> Action:
    return Action(
        name=f"git/{name}", label=label, fields=fields, confirm=confirm, group=group, icon=icon
    )


class _PrView(Model):
    """What git reads of a `pull_request`: whose, from which branch, and its state."""

    id: str
    url: str | None = None
    state: str | None = None
    repo: str
    handle: str
    head: str
    head_oid: str | None = None
    mine: bool
    merged_at: str | None = None

    @property
    def subject(self) -> Ref:
        return Ref(kind=PR, id=self.id)


class _StackBranchView(Model):
    """What git reads of a `git_stack.stack_branch`: which branch, and whether git-stack sees
    it merged."""

    repo_dir: str
    name: str
    stack_status: str | None = None


class Builder:
    def __init__(
        self, config: GitConfig, now: datetime, snapshot: LocalSnapshot | None, graph: Graph
    ):
        self.config = config
        self.now = now
        self.local = snapshot
        self.graph = graph
        self.stacked: dict[tuple[str, str], _StackBranchView] = {
            (b.repo_dir, b.name): b for b in graph.view(STACK_BRANCH, _StackBranchView)
        }
        """Branches git-stack tracks, by checkout and name."""

    def _prs_from(self, repo_dir: str, name: str) -> list[_PrView]:
        """The PRs from the branch, mine or not: those the graph relates to it (github does,
        by its name, its upstream's or its commit)."""
        return self.graph.view_related(branch_ref(repo_dir, name), PR, _PrView)

    def _prs_for(self, repo_dir: str, name: str) -> list[_PrView]:
        """My PRs from the branch."""
        return [pr for pr in self._prs_from(repo_dir, name) if pr.mine]

    def _pr(self, repo_dir: str, name: str, state: str) -> _PrView | None:
        """My PR from the branch in `state`, e.g. "open" or "merged"."""
        return next((pr for pr in self._prs_for(repo_dir, name) if pr.state == state), None)

    def _is_my_branch(self, repo_dir: str, branch: Branch) -> bool:
        """Mine if a PR of mine comes from it, if I have commits on it that aren't on the
        default branch, or by its name's prefix. Without prefixes, a branch whose commits
        were counted and none are mine (a review checkout) isn't; one too old to count is."""
        if self._prs_for(repo_dir, branch.name) or branch.my_commits:
            return True
        prefixes = self.config.my_branch_prefixes
        if prefixes:
            return any(branch.name.startswith(p) for p in prefixes)
        return branch.my_commits is None

    def _has_my_work(self, repo: LocalRepo, branch: Branch) -> bool:
        """Whether Local work shows the branch's unpushed commits: it's mine, or git-stack
        tracks it (stacks are built on my own branches)."""
        return (repo.dir, branch.name) in self.stacked or self._is_my_branch(repo.dir, branch)

    def _stack_cleans(self, repo: LocalRepo, branch: Branch) -> bool:
        """Whether git-stack sees the branch merged: git_stack's Clean deletes it and untracks
        it too, where `git branch -D` would leave git-stack tracking a branch that's gone."""
        stacked = self.stacked.get((repo.dir, branch.name))
        return stacked is not None and stacked.stack_status == "merged"

    def local_work(self) -> Panel:
        items: list[Item] = []
        dirty: dict[tuple[str, str], int] = {}
        """Where in `items` each branch's uncommitted-changes item is, so its unpushed
        commits join it: a branch is one item."""
        for repo in self.local.repos if self.local else []:
            for wt in repo.worktrees:
                if wt.modified or wt.untracked:
                    pr = self._pr(repo.dir, wt.branch, "open") if wt.branch else None
                    actions: list[Action] = []
                    if wt.modified:
                        actions.append(
                            action(
                                "wip_commit", "WIP commit", {"repo": repo.dir, "worktree": wt.path}
                            )
                        )
                    if wt.branch:
                        dirty[(repo.dir, wt.branch)] = len(items)
                    items.append(
                        Item(
                            title=f"{wt.branch or 'detached HEAD'}: {wt.modified} modified,"
                            f" {wt.untracked} untracked",
                            url=pr.url if pr else None,
                            subtitle=wt.path,
                            badges=[Badge(text="uncommitted", tone="warn")],
                            rank=2,
                            actions=actions,
                            subjects=([branch_ref(repo.dir, wt.branch)] if wt.branch else [])
                            + ([pr.subject] if pr else []),
                        )
                    )
            for branch in repo.branches:
                if branch.name == repo.default_branch:
                    continue
                if item := self._behind_github(repo, branch):
                    items.append(item)
                    continue
                if not self._has_my_work(repo, branch):
                    continue
                reason = None
                if branch.ahead:
                    reason = f"{branch.ahead} unpushed commits"
                elif branch.upstream is None and branch.ahead_of_default:
                    reason = f"{branch.ahead_of_default} commits, never pushed"
                if not reason:
                    continue
                pr = self._pr(repo.dir, branch.name, "open")
                actions = []
                if not branch.behind:
                    actions.append(
                        action("push_branch", "Push", {"repo": repo.dir, "branch": branch.name})
                    )
                details: list[str] = []
                if branch.behind and branch.upstream:
                    details.append(
                        f"{branch.behind} behind {branch.upstream}: a plain push would be rejected"
                    )
                item = Item(
                    title=f"{branch.name}: {reason}",
                    url=pr.url if pr else None,
                    subtitle=f"{repo.dir} · last commit {age(self.now, branch.committed_at)} ago",
                    badges=["has PR"] if pr else ["no PR"],
                    details=details,
                    actions=actions,
                    subjects=[branch_ref(repo.dir, branch.name)] + ([pr.subject] if pr else []),
                )
                if (index := dirty.get((repo.dir, branch.name))) is not None:
                    uncommitted = items[index]
                    item = uncommitted.model_copy(
                        update={
                            "title": f"{uncommitted.title}, {reason}",
                            "badges": [*uncommitted.badges, *item.badges],
                            "details": details,
                            "actions": [*uncommitted.actions, *actions],
                        }
                    )
                    items[index] = item
                else:
                    items.append(item)
        return Panel(
            key="local_work",
            priority=Band.PUSH,
            title="Local work",
            description="Uncommitted changes in worktrees, branches with unpushed commits, and"
            " checkouts of PRs that moved on GitHub.",
            items=items,
        )

    def _behind_github(self, repo: LocalRepo, branch: Branch) -> Item | None:
        """An item for a branch with nothing to push whose open PR (mine, or one I review)
        is at another commit on GitHub: someone pushed to it since it was last pulled.
        Read from github's PRs, so it needs no fetch."""
        pr = next((p for p in self._prs_from(repo.dir, branch.name) if p.state == "open"), None)
        if (
            pr is None
            or branch.ahead
            or branch.upstream is None
            or pr.head_oid is None
            or branch.sha in (None, pr.head_oid)
        ):
            return None
        return Item(
            title=f"{branch.name}: PR {pr.handle} moved on GitHub; pull it",
            url=pr.url,
            subtitle=f"{repo.dir} · last commit here {age(self.now, branch.committed_at)} ago",
            badges=["behind GitHub"],
            subjects=[branch_ref(repo.dir, branch.name), pr.subject],
            actions=[action("pull_branch", "Pull", {"repo": repo.dir, "branch": branch.name})],
        )

    def cleanup(self) -> Panel:
        items: list[Item] = []
        targets: list[str] = []
        for repo in self.local.repos if self.local else []:
            for b in repo.branches:
                if self._stack_cleans(repo, b):
                    continue
                merged = self._pr(repo.dir, b.name, "merged")
                if merged:
                    when = datetime.fromisoformat(merged.merged_at) if merged.merged_at else None
                    title = f"{b.name}: PR merged {age(self.now, when)} ago"
                    subtitle = repo.dir
                elif b.gone and not b.ahead and self._is_my_branch(repo.dir, b):
                    title = f"{b.name}: upstream gone"
                    subtitle = f"{repo.dir} · last commit {age(self.now, b.committed_at)} ago"
                else:
                    continue
                targets.append(branch_id(repo.dir, b.name))
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
                                group="Disposition",
                                icon="🗑️",
                            )
                        ],
                        # The PR first: it keys Act now's feedback and anchors the item. The branch is
                        # what Put on ice finds.
                        subjects=[merged.subject, branch_ref(repo.dir, b.name)]
                        if merged
                        else [branch_ref(repo.dir, b.name)],
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
            priority=Band.CLEANUP,
            act_now=True,
            signals=Signals(verb="Delete the branch", task="delete_the_branch", effort="minutes"),
            title="Cleanup",
            description="Local branches whose PR merged or whose remote branch is gone.",
            items=items,
            actions=actions,
        )

    def panels(self) -> list[Panel]:
        return [self.local_work(), self.cleanup()]


def branch_ref(repo_dir: str, branch: str) -> Ref:
    return Ref(kind=BRANCH, id=branch_id(repo_dir, branch))


def restore_iced_action(item: Item, graph: Graph) -> Action | None:
    """A Restore branches button for notes' On ice item whose branches git no longer has: it
    recreates them at the commits they were iced at."""
    if not any(graph.hides(ref.kind) for ref in item.subjects):
        return None
    gone = [
        ref.id for ref in item.subjects if ref.kind == BRANCH and graph.get(BRANCH, ref.id) is None
    ]
    if not gone:
        return None
    return action("restore_together", "Restore branches", {"targets": "\n".join(gone)})


def delete_iced_action(item: Item, graph: Graph) -> Action | None:
    """A Delete branches button for an item about what hides its branches, i.e. notes' On ice
    item: the ones still here, all or none, each checked for commits since it was iced."""
    if not any(graph.hides(ref.kind) for ref in item.subjects):
        return None
    branches = [ref for ref in item.subjects if ref.kind == BRANCH]
    here = [ref.id for ref in branches if graph.get(BRANCH, ref.id) is not None]
    if not here:
        return None
    names = ", ".join(branch_parts(ref_id)[1] for ref_id in here)
    return action(
        "delete_together",
        "Delete branches",
        {"targets": "\n".join(here)},
        confirm=f"Delete the local branches {names}? Remote branches and PRs are not touched,"
        " and they stay on ice with the commits they were iced at.",
    )
