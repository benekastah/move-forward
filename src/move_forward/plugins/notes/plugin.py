"""The notes plugin: work entries with their todos, and what's on ice, in its own tables.

`mfd notes import` reads them once from the move-forward skill's markdown files.
"""

import argparse
from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from ...graph import Entity, Graph
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, CliHandler, Context, Plugin
from . import actions, cli, prune
from .config import NotesConfig
from .kinds import EntryAttrs, IcedBranchAttrs
from .panels import ENTRY, ICED_BRANCH, Builder, entities
from .repository import MIGRATIONS, NotesRepository


def _builder(ctx: Context[NotesConfig], graph: Graph) -> Builder:
    notes = NotesRepository(ctx.db)
    return Builder(ctx.now, graph, notes.entries(), notes.ices())


class NotesPlugin(Plugin[NotesConfig, BaseModel]):
    name = "notes"
    title = "Notes"
    config_model = NotesConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {ENTRY: EntryAttrs, ICED_BRANCH: IcedBranchAttrs}
    cli_help = "import the skill's markdown notes, or export them back"

    def entities(self, ctx: Context[NotesConfig], snapshot: BaseModel | None) -> list[Entity]:
        notes = NotesRepository(ctx.db)
        return entities(notes.entries(), notes.ices())

    def panels(
        self, ctx: Context[NotesConfig], snapshot: BaseModel | None, graph: Graph
    ) -> list[Panel]:
        builder = _builder(ctx, graph)
        return [builder.todos(), builder.iced()]

    def item_actions(self, ctx: Context[NotesConfig], item: Item, graph: Graph) -> list[Action]:
        builder = _builder(ctx, graph)
        return [a for a in (builder.put_on_ice(item), builder.add_todo(item)) if a]

    def kept_item(self, ctx: Context[NotesConfig], graph: Graph, key: str) -> Item | None:
        """The todo `todo_done` just ticked off, by id, with its box ticked."""
        return _builder(ctx, graph).kept_todo(int(key)) if key.isdigit() else None

    def prune(self, ctx: Context[NotesConfig], graph: Graph) -> list[str]:
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        return prune.prune(NotesRepository(ctx.db), graph, cutoff)

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS
