"""The git-stack plugin."""

import shutil
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ...graph import Entity, Graph
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, Context, Plugin
from . import actions, changes, client
from .config import GitStackConfig
from .kinds import StackBranchAttrs
from .models import GitStackSnapshot
from .panels import STACK_BRANCH, Builder, entities, repo_dirs, untrack_iced_action
from .repository import MIGRATIONS, StackRepository

FINGERPRINT = "stacks_fingerprint"
"""The kv key of the last `changes.fingerprint`."""
TEMPLATE = """\
# refresh_seconds = 300                 # how often it refreshes regardless of git's changes
"""


class GitStackPlugin(Plugin[GitStackConfig, GitStackSnapshot]):
    name = "git_stack"
    title = "git-stack"

    def init_suggested(self) -> bool:
        """Only when git-stack is installed, i.e. `git stack` would run."""
        return shutil.which("git-stack") is not None

    config_model = GitStackConfig
    init_template = TEMPLATE
    requires: ClassVar[list[str]] = ["git"]
    watches: ClassVar[list[str]] = ["git"]
    snapshot_model = GitStackSnapshot
    collects_after_graph = True
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {STACK_BRANCH: StackBranchAttrs}

    def collect_after(self, ctx: Context[GitStackConfig], graph: Graph) -> GitStackSnapshot:
        """`git stack info` in each repo git has branches in."""
        return client.collect([Path(d) for d in repo_dirs(graph)])

    def store(self, ctx: Context[GitStackConfig], snapshot: GitStackSnapshot) -> None:
        StackRepository(ctx.db).replace(snapshot)

    def changed(self, ctx: Context[GitStackConfig], graph: Graph) -> bool:
        """Whether a stack may have moved since the last look (see `changes`): asked right
        after each refresh of git, rather than collecting with every one."""
        now = changes.fingerprint(graph, repo_dirs(graph))
        before = ctx.kv.get(FINGERPRINT)
        if now != before:
            ctx.kv.set(FINGERPRINT, now)
        return before is not None and now != before

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
