"""The git-stack plugin."""

from pathlib import Path
from typing import ClassVar

from ...domain import GitStackSnapshot
from ...graph import Entity, Graph
from ...page import Panel
from ...plugin import ActionHandler, Context, Plugin
from . import actions, client
from .config import GitStackConfig
from .panels import Builder, entities, repo_dirs


class GitStackPlugin(Plugin[GitStackConfig, GitStackSnapshot]):
    name = "git_stack"
    title = "git-stack"
    config_model = GitStackConfig
    requires: ClassVar[list[str]] = ["git"]
    snapshot_model = GitStackSnapshot
    collects_after_graph = True

    def collect_after(self, ctx: Context[GitStackConfig], graph: Graph) -> GitStackSnapshot:
        """`git stack info` in each repo git has branches in."""
        return client.collect([Path(d) for d in repo_dirs(graph)])

    def entities(
        self, ctx: Context[GitStackConfig], snapshot: GitStackSnapshot | None
    ) -> list[Entity]:
        return entities(snapshot)

    def panels(
        self, ctx: Context[GitStackConfig], snapshot: GitStackSnapshot | None, graph: Graph
    ) -> list[Panel]:
        return [Builder(snapshot, graph).stacks()]

    def actions(self) -> dict[str, ActionHandler]:
        return {"stack_clean": actions.stack_clean}
