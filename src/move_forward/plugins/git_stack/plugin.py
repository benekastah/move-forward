"""The git_stack plugin."""

import shutil
from pathlib import Path
from typing import ClassVar

from ...api import (
    API_VERSION,
    ActionHandler,
    Changed,
    ContextRequest,
    Entity,
    Event,
    Init,
    ItemAdditions,
    ItemAdditionsFor,
    ItemRequest,
    KindSpec,
    Migration,
    Panel,
    Plugin,
    RefreshRequest,
    Request,
)
from . import actions, changes, client, events
from .config import GitStackConfig
from .kinds import StackBranchAttrs
from .models import GitStackSnapshot
from .panels import STACK_BRANCH, Builder, entities, repo_dirs, untrack_iced_action
from .repository import MIGRATIONS, StackRepository


class GitStackPlugin(Plugin[GitStackConfig, GitStackSnapshot]):
    name = "git_stack"
    title = "Git Stack"
    config_model = GitStackConfig
    api_version = API_VERSION
    init = Init(suggested=shutil.which("git-stack") is not None, example_on=True)
    """Suggested only when git stack is installed, i.e. `git stack` would run."""
    requires: ClassVar[list[str]] = ["git"]
    watches: ClassVar[list[str]] = ["git"]
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {STACK_BRANCH: KindSpec(StackBranchAttrs)}
    notification_kinds: ClassVar[dict[str, str]] = events.KINDS

    def collect(self, req: RefreshRequest[GitStackConfig]) -> GitStackSnapshot:
        """`git stack info` in each repo git has branches in."""
        graph = req.graph
        return client.collect([Path(d) for d in repo_dirs(graph)])

    def changed(self, req: RefreshRequest[GitStackConfig]) -> Changed:
        """Whether a stack may have moved since the last look (see `changes`): asked right
        after each refresh of git, rather than collecting with every one. Its branches are
        compared with the graph at the last look; the config files, where git stack records
        its stacks and no graph shows, are the fingerprint core compares with the last."""
        graph, previous = req.graph, req.previous.graph
        dirs = repo_dirs(graph)
        moved = previous is not None and (
            changes.branches(previous, dirs) != changes.branches(graph, dirs)
        )
        return Changed(
            fingerprint=changes.configs(dirs), everything=moved, reason="a stack may have moved"
        )

    def snapshots(self, req: ContextRequest[GitStackConfig]) -> StackRepository:
        """What the last refresh found; with no rows yet, nothing tracked (or no refresh)."""
        return StackRepository(req.ctx.db)

    def entities(self, req: Request[GitStackConfig, GitStackSnapshot]) -> list[Entity]:
        snapshot = req.snapshot
        return entities(snapshot)

    def panels(self, req: Request[GitStackConfig, GitStackSnapshot]) -> list[Panel]:
        snapshot, graph = req.snapshot, req.graph
        return [Builder(snapshot, graph).stacks()]

    def events(self, req: Request[GitStackConfig, GitStackSnapshot]) -> list[Event]:
        before, after = req.previous.snapshot, req.snapshot
        assert before is not None and after is not None
        return events.changes(before, after)

    def item_actions(self, req: Request[GitStackConfig, GitStackSnapshot]) -> ItemAdditionsFor:
        """Untrack the branches an On ice item put on ice."""
        graph = req.graph

        def additions(each: ItemRequest) -> ItemAdditions:
            action = untrack_iced_action(each.item, graph)
            return ItemAdditions(actions=[action] if action else [])

        return additions

    def actions(self) -> dict[str, ActionHandler]:
        return {
            "stack_clean": actions.stack_clean,
            "stack_restack": actions.stack_restack,
            "untrack": actions.untrack,
        }
