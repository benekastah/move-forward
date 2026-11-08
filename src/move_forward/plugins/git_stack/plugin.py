"""The git-stack plugin."""

import shutil
from pathlib import Path
from typing import ClassVar

from ...api import (
    API_VERSION,
    ActionHandler,
    ActNowRequest,
    ChangedRequest,
    CollectAfterRequest,
    EntitiesRequest,
    Entity,
    Event,
    EventsRequest,
    Item,
    ItemActionsRequest,
    ItemAdditions,
    KindSpec,
    LoadRequest,
    Migration,
    Panel,
    PanelsRequest,
    Plugin,
    StoreRequest,
)
from . import actions, changes, client, events
from .config import GitStackConfig
from .kinds import StackBranchAttrs
from .models import GitStackSnapshot
from .panels import CLEAN, STACK_BRANCH, Builder, entities, repo_dirs, untrack_iced_action
from .repository import MIGRATIONS, StackRepository

FINGERPRINT = "stacks_fingerprint"
"""The kv key of the last `changes.fingerprint`."""
TEMPLATE = """\
# refresh_seconds = 1800                # how often it refreshes regardless of git's changes
"""


class GitStackPlugin(Plugin[GitStackConfig, GitStackSnapshot]):
    name = "git_stack"
    title = "git-stack"
    config_model = GitStackConfig
    api_version = API_VERSION
    init_template = TEMPLATE
    requires: ClassVar[list[str]] = ["git"]
    watches: ClassVar[list[str]] = ["git"]
    snapshot_model = GitStackSnapshot
    collects_after_graph = True
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {STACK_BRANCH: KindSpec(StackBranchAttrs)}
    notification_kinds: ClassVar[dict[str, str]] = events.KINDS

    def init_suggested(self) -> bool:
        """Only when git-stack is installed, i.e. `git stack` would run."""
        return shutil.which("git-stack") is not None

    def collect_after(self, req: CollectAfterRequest[GitStackConfig]) -> GitStackSnapshot:
        """`git stack info` in each repo git has branches in."""
        graph = req.graph
        return client.collect([Path(d) for d in repo_dirs(graph)])

    def store(self, req: StoreRequest[GitStackConfig, GitStackSnapshot]) -> None:
        ctx, snapshot = req.ctx, req.snapshot
        StackRepository(ctx.db).replace(snapshot)

    def changed(self, req: ChangedRequest[GitStackConfig]) -> bool:
        """Whether a stack may have moved since the last look (see `changes`): asked right
        after each refresh of git, rather than collecting with every one."""
        ctx, graph = req.ctx, req.graph
        now = changes.fingerprint(graph, repo_dirs(graph))
        before = ctx.kv.get(FINGERPRINT)
        if now != before:
            ctx.kv.set(FINGERPRINT, now)
        return before is not None and now != before

    def load(self, req: LoadRequest[GitStackConfig]) -> GitStackSnapshot | None:
        """What the last refresh found; with no rows yet, nothing tracked (or no refresh)."""
        ctx = req.ctx
        return StackRepository(ctx.db).load()

    def entities(self, req: EntitiesRequest[GitStackConfig, GitStackSnapshot]) -> list[Entity]:
        snapshot = req.snapshot
        return entities(snapshot)

    def panels(self, req: PanelsRequest[GitStackConfig, GitStackSnapshot]) -> list[Panel]:
        snapshot, graph = req.snapshot, req.graph
        return [Builder(snapshot, graph).stacks()]

    def act_now(self, req: ActNowRequest[GitStackConfig, GitStackSnapshot]) -> list[Item]:
        """Merged branches, quick to clean up, as git nominates the merged branches it can
        delete; the rest of Stacks is news rather than something to do. One per repo: its
        Clean cleans them all, so more would only take Act now's places with the same button."""
        # Not one on ice: core would leave it out, and with it the repo's only Clean.
        merged = [
            item
            for panel in req.panels
            for item in panel.items
            if item.signals and not any(req.graph.hidden(ref) for ref in item.subjects)
        ]
        by_repo: dict[str, list[Item]] = {}
        for item in merged:
            repo = next((a.fields["repo"] for a in item.actions if a.name == CLEAN), "")
            by_repo.setdefault(repo, []).append(item)
        return [
            items[0].model_copy(
                update={"details": [*items[0].details, f"and {len(items) - 1} more merged here"]}
            )
            if len(items) > 1
            else items[0]
            for items in by_repo.values()
        ]

    def events(self, req: EventsRequest[GitStackConfig, GitStackSnapshot]) -> list[Event]:
        before, after = req.before, req.after
        return events.changes(before, after)

    def item_actions(self, req: ItemActionsRequest[GitStackConfig]) -> ItemAdditions:
        action = untrack_iced_action(req.item, req.graph)
        return ItemAdditions(actions=[action] if action else [])

    def actions(self) -> dict[str, ActionHandler]:
        return {
            "stack_clean": actions.stack_clean,
            "stack_restack": actions.stack_restack,
            "untrack": actions.untrack,
        }
