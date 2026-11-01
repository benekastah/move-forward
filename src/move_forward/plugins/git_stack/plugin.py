"""The git-stack plugin."""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ...graph import Entity, Graph
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, Context, Plugin
from . import actions, client
from .config import GitStackConfig
from .kinds import StackBranchAttrs
from .models import GitStackSnapshot
from .panels import BRANCH, Builder, entities, repo_dirs, untrack_iced_action
from .repository import MIGRATIONS, StackRepository


class GitStackPlugin(Plugin[GitStackConfig, GitStackSnapshot]):
    name = "git_stack"
    title = "git-stack"
    config_model = GitStackConfig
    requires: ClassVar[list[str]] = ["git"]
    snapshot_model = GitStackSnapshot
    collects_after_graph = True
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {BRANCH: StackBranchAttrs}
    """git's kind; git_stack's entities of it carry only what it adds, merged into git's."""

    def collect_after(self, ctx: Context[GitStackConfig], graph: Graph) -> GitStackSnapshot:
        """`git stack info` in each repo git has branches in."""
        return client.collect([Path(d) for d in repo_dirs(graph)])

    def store(self, ctx: Context[GitStackConfig], snapshot: GitStackSnapshot) -> None:
        StackRepository(ctx.db).replace(snapshot)

    def load(self, ctx: Context[GitStackConfig]) -> GitStackSnapshot | None:
        """What the last refresh found; with no rows yet, nothing tracked (or no refresh)."""
        return StackRepository(ctx.db).load()

    def entities(
        self, ctx: Context[GitStackConfig], snapshot: GitStackSnapshot | None
    ) -> list[Entity]:
        return entities(snapshot)

    def panels(
        self, ctx: Context[GitStackConfig], snapshot: GitStackSnapshot | None, graph: Graph
    ) -> list[Panel]:
        return [Builder(snapshot, graph).stacks()]

    def item_actions(self, ctx: Context[GitStackConfig], item: Item, graph: Graph) -> list[Action]:
        action = untrack_iced_action(item, graph)
        return [action] if action else []

    def actions(self) -> dict[str, ActionHandler]:
        return {"stack_clean": actions.stack_clean, "untrack": actions.untrack}
