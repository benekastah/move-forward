"""The git plugin."""

from ...domain import LocalSnapshot
from ...graph import Entity, Graph, Ref
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, Context, Plugin
from . import actions, client
from .config import GitConfig
from .panels import Builder, branch_ref, delete_iced_action

REPO = "repo"


class GitPlugin(Plugin[GitConfig, LocalSnapshot]):
    name = "git"
    title = "Local"
    config_model = GitConfig
    snapshot_model = LocalSnapshot

    def collect(self, ctx: Context[GitConfig]) -> LocalSnapshot:
        return client.collect(
            ctx.config.repos,
            ctx.now,
            ctx.config.commit_history_days,
            ctx.config.ignored_worktree_globs,
        )

    def entities(self, ctx: Context[GitConfig], snapshot: LocalSnapshot | None) -> list[Entity]:
        """A `repo` per checkout on GitHub, and a `branch` per local branch."""
        entities: list[Entity] = []
        for repo in snapshot.repos if snapshot else []:
            repo_ref = Ref(kind=REPO, id=repo.github_repo) if repo.github_repo else None
            if repo_ref:
                entities.append(
                    Entity(
                        ref=repo_ref,
                        title=repo.github_repo or repo.dir,
                        attrs={
                            "dir": repo.dir,
                            "default_branch": repo.default_branch,
                            "my_dirs": list(repo.my_dirs),
                        },
                    )
                )
            checked_out = {wt.branch: wt.path for wt in repo.worktrees if wt.branch}
            for branch in repo.branches:
                entities.append(
                    Entity(
                        ref=branch_ref(repo.dir, branch.name),
                        title=branch.name,
                        refs=[repo_ref] if repo_ref else [],
                        attrs={
                            "repo_dir": repo.dir,
                            "sha": branch.sha,
                            "ahead": branch.ahead,
                            "behind": branch.behind,
                            "gone": branch.gone,
                            "checked_out_in": checked_out.get(branch.name),
                        },
                    )
                )
        return entities

    def panels(
        self, ctx: Context[GitConfig], snapshot: LocalSnapshot | None, graph: Graph
    ) -> list[Panel]:
        return Builder(ctx.config, ctx.now, snapshot, graph).panels()

    def item_actions(self, ctx: Context[GitConfig], item: Item, graph: Graph) -> list[Action]:
        action = delete_iced_action(item, graph)
        return [action] if action else []

    def actions(self) -> dict[str, ActionHandler]:
        return {
            "delete_branch": actions.delete_branch,
            "delete_branches": actions.delete_branches,
            "delete_together": actions.delete_together,
            "push_branch": actions.push_branch,
            "wip_commit": actions.wip_commit,
        }
