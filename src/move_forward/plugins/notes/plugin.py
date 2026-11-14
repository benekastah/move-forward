"""The notes plugin: work entries with their todos, and what's on ice, in its own tables."""

import argparse
from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from ...api import (
    API_VERSION,
    ActionHandler,
    CliHandler,
    Context,
    Entity,
    Graph,
    ItemAdditions,
    ItemAdditionsFor,
    ItemRequest,
    KindSpec,
    Migration,
    Panel,
    Plugin,
    Request,
)
from ...api.kinds import FAMILIES
from . import actions, cli, prune
from .config import NotesConfig
from .covers import covers
from .kinds import EntryAttrs, IcedAttrs, TodoAttrs
from .panels import ENTRY, ICED, TODO, Builder, entities
from .repository import MIGRATIONS, NotesRepository

EMPTY_NOTES = (
    "No notes yet: New todo starts one here, and `mfd notes add-entry <title>` starts an entry."
)
"""What the Todos panel says while the notes are empty."""


def _builder(ctx: Context[NotesConfig], graph: Graph) -> Builder:
    notes = NotesRepository(ctx.db)
    return Builder(ctx.now, graph, notes.entries(), notes.ices(), ctx.today)


class NotesPlugin(Plugin[NotesConfig, BaseModel]):
    name = "notes"
    title = "Notes"
    always_on = True
    config_model = NotesConfig
    api_version = API_VERSION
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        # Done entries and todos aren't published: one that's gone is done.
        ENTRY: KindSpec(EntryAttrs, linking=True, gone_means_done=True, resolvable=True),
        ICED: KindSpec(IcedAttrs, hides=True),
        TODO: KindSpec(TodoAttrs, gone_means_done=True),
    }

    def entities(self, req: Request[NotesConfig, BaseModel]) -> list[Entity]:
        ctx = req.ctx
        notes = NotesRepository(ctx.db)
        return entities(notes.entries(), notes.ices(), notes.covers())

    def after_collect(self, req: Request[NotesConfig, BaseModel]) -> None:
        """Give links and what's on ice the ids their entities have now (a ticket kept by its
        key gets its URL once jira can say it), then find again what each act of icing
        covers besides its items, e.g. new PRs from its branches."""
        ctx, graph = req.ctx, req.graph
        notes = NotesRepository(ctx.db)
        if moved := notes.rekey(list(FAMILIES), graph.identify):
            ctx.log.info("gave %s link(s) and iced item(s) their entities' ids", moved)
        ices = notes.ices()
        notes.replace_covers([i.id for i in ices], covers(graph, ices))

    def panels(self, req: Request[NotesConfig, BaseModel]) -> list[Panel]:
        ctx, graph = req.ctx, req.graph
        builder = _builder(ctx, graph)
        todos = builder.todos()
        if NotesRepository(ctx.db).is_empty():
            todos = todos.model_copy(update={"description": EMPTY_NOTES})
        return [todos, builder.iced()]

    def item_actions(self, req: Request[NotesConfig, BaseModel]) -> ItemAdditionsFor:
        """Put on ice and Add todo on other plugins' items, from the notes read once for the
        page."""
        builder = _builder(req.ctx, req.graph)

        def additions(each: ItemRequest) -> ItemAdditions:
            found = (
                builder.put_on_ice(
                    each.item, group="Act now" if each.surface == "act_now" else "Disposition"
                ),
                builder.add_todo(each.item),
            )
            return ItemAdditions(actions=[a for a in found if a])

        return additions

    def prune(self, req: Request[NotesConfig, BaseModel]) -> list[str]:
        ctx, graph = req.ctx, req.graph
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        return prune.prune(NotesRepository(ctx.db), graph, cutoff)

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        """read and change the notes"""
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS
