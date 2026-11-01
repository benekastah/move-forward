"""The notes plugin: work entries with their todos, and what's on ice, in its own tables.

`mfd notes import` reads them once from the move-forward skill's markdown files.
"""

import argparse
from collections.abc import Callable
from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from ...graph import Entity, Graph
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, CliHandler, Context, Plugin
from . import actions, cli, prune
from .config import NotesConfig
from .covers import covers
from .kinds import EntryAttrs, IcedAttrs
from .panels import ENTRY, ICED, TODOS, Builder, entities
from .repository import MIGRATIONS, NotesRepository


def _builder(ctx: Context[NotesConfig], graph: Graph) -> Builder:
    notes = NotesRepository(ctx.db)
    return Builder(ctx.now, graph, notes.entries(), notes.ices())


class NotesPlugin(Plugin[NotesConfig, BaseModel]):
    name = "notes"
    title = "Notes"
    always_on = True
    config_model = NotesConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    kinds: ClassVar[dict[str, type[BaseModel]]] = {
        ENTRY: EntryAttrs,
        ICED: IcedAttrs,
    }
    linking: ClassVar[dict[str, str | None]] = {ENTRY: None}
    hides: ClassVar[list[str]] = [ICED]
    cli_help = "import the skill's markdown notes, or export them back"

    def entities(self, ctx: Context[NotesConfig], snapshot: BaseModel | None) -> list[Entity]:
        notes = NotesRepository(ctx.db)
        return entities(notes.entries(), notes.ices(), notes.covers())

    def after_collect(self, ctx: Context[NotesConfig], graph: Graph) -> None:
        """Find again what each act of icing covers besides its items, e.g. new PRs from its
        branches."""
        notes = NotesRepository(ctx.db)
        ices = notes.ices()
        notes.replace_covers([i.id for i in ices], covers(graph, ices))

    def panels(
        self, ctx: Context[NotesConfig], snapshot: BaseModel | None, graph: Graph
    ) -> list[Panel]:
        builder = _builder(ctx, graph)
        return [builder.todos(), builder.iced()]

    def act_now(
        self,
        ctx: Context[NotesConfig],
        snapshot: BaseModel | None,
        graph: Graph,
        panels: list[Panel],
    ) -> list[Item]:
        """Close-outs: entries whose open todos are all quick, finishing the entry."""
        return [i for p in panels if p.key == TODOS for i in p.items if i.signals]

    def prepare_item_actions(
        self, ctx: Context[NotesConfig], graph: Graph
    ) -> Callable[[Item], list[Action]]:
        """Put on ice and Add todo on other plugins' items, from the notes read once for the
        page."""
        builder = _builder(ctx, graph)
        return lambda item: [a for a in (builder.put_on_ice(item), builder.add_todo(item)) if a]

    def kept_item(
        self, ctx: Context[NotesConfig], graph: Graph, key: str
    ) -> tuple[str, Item] | None:
        """The todo `todo_done` just ticked off, by id, with its box ticked."""
        todo = _builder(ctx, graph).kept_todo(int(key)) if key.isdigit() else None
        return (TODOS, todo) if todo else None

    def prune(self, ctx: Context[NotesConfig], graph: Graph) -> list[str]:
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        return prune.prune(NotesRepository(ctx.db), graph, cutoff)

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        return actions.ACTIONS
