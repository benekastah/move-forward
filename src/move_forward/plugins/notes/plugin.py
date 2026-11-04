"""The notes plugin: work entries with their todos, and what's on ice, in its own tables.

`mfd notes import` reads them once from the move-forward skill's markdown files.
"""

import argparse
from collections.abc import Callable
from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from ...api import (
    ActionHandler,
    ActNowRequest,
    AfterCollectRequest,
    CliHandler,
    Context,
    EntitiesRequest,
    Entity,
    Graph,
    Item,
    ItemActionsRequest,
    ItemAdditions,
    KeptItemRequest,
    KindSpec,
    Panel,
    PanelsRequest,
    Plugin,
    PrepareItemActionsRequest,
    PruneRequest,
)
from ...api.kinds import FAMILIES
from . import actions, cli, prune
from .config import NotesConfig
from .covers import covers
from .kinds import EntryAttrs, IcedAttrs, TodoAttrs
from .panels import ENTRY, ICED, TODO, TODOS, Builder, entities
from .repository import MIGRATIONS, NotesRepository

EMPTY_NOTES = (
    "No notes yet: `mfd notes import <dir>` reads the skill's work-notes.md and on-ice.md,"
    " and `mfd notes add-entry <title>` starts one."
)
"""What the Todos panel says while the notes are empty."""


def _builder(ctx: Context[NotesConfig], graph: Graph) -> Builder:
    notes = NotesRepository(ctx.db)
    return Builder(ctx.now, graph, notes.entries(), notes.ices())


class NotesPlugin(Plugin[NotesConfig, BaseModel]):
    name = "notes"
    title = "Notes"
    always_on = True
    config_model = NotesConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        # Done entries and todos aren't published: one that's gone is done.
        ENTRY: KindSpec(EntryAttrs, linking=None, gone_means_done=True, resolvable=True),
        ICED: KindSpec(IcedAttrs, hides=True),
        TODO: KindSpec(TodoAttrs, gone_means_done=True),
    }
    cli_help = "import the skill's markdown notes, or export them back"

    def entities(self, req: EntitiesRequest[NotesConfig, BaseModel]) -> list[Entity]:
        ctx = req.ctx
        notes = NotesRepository(ctx.db)
        return entities(notes.entries(), notes.ices(), notes.covers())

    def after_collect(self, req: AfterCollectRequest[NotesConfig]) -> None:
        """Give links and what's on ice the ids their entities have now (a ticket kept by its
        key gets its URL once jira can say it), then find again what each act of icing
        covers besides its items, e.g. new PRs from its branches."""
        ctx, graph = req.ctx, req.graph
        notes = NotesRepository(ctx.db)
        if moved := notes.rekey(list(FAMILIES), graph.identify):
            ctx.log.info("gave %s link(s) and iced item(s) their entities' ids", moved)
        ices = notes.ices()
        notes.replace_covers([i.id for i in ices], covers(graph, ices))

    def panels(self, req: PanelsRequest[NotesConfig, BaseModel]) -> list[Panel]:
        ctx, graph = req.ctx, req.graph
        builder = _builder(ctx, graph)
        todos = builder.todos()
        if NotesRepository(ctx.db).is_empty():  # e.g. just upgraded, before an import
            todos = todos.model_copy(update={"description": EMPTY_NOTES})
        return [todos, builder.iced()]

    def act_now(self, req: ActNowRequest[NotesConfig, BaseModel]) -> list[Item]:
        """Close-outs: entries whose open todos are all quick, finishing the entry."""
        panels = req.panels
        return [i for p in panels if p.key == TODOS for i in p.items if i.signals]

    def prepare_item_actions(
        self, req: PrepareItemActionsRequest[NotesConfig]
    ) -> Callable[[ItemActionsRequest[NotesConfig]], ItemAdditions]:
        """Put on ice and Add todo on other plugins' items, from the notes read once for the
        page."""
        builder = _builder(req.ctx, req.graph)

        def additions(each: ItemActionsRequest[NotesConfig]) -> ItemAdditions:
            found = (builder.put_on_ice(each.item), builder.add_todo(each.item))
            return ItemAdditions(actions=[a for a in found if a])

        return additions

    def kept_item(self, req: KeptItemRequest[NotesConfig]) -> tuple[str, Item] | None:
        """The todo `todo_done` just ticked off, by id, with its box ticked."""
        ctx, graph, key = req.ctx, req.graph, req.key
        todo = _builder(ctx, graph).kept_todo(int(key)) if key.isdigit() else None
        return (TODOS, todo) if todo else None

    def prune(self, req: PruneRequest[NotesConfig]) -> list[str]:
        ctx, graph = req.ctx, req.graph
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        return prune.prune(NotesRepository(ctx.db), graph, cutoff)

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS
