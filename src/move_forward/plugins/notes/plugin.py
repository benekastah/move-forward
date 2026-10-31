"""The notes plugin.

The work notes and stacks on ice are still parsed from the skill's markdown into core's tables
on every refresh; this plugin reads them from there.
"""

import argparse
from typing import ClassVar

from pydantic import BaseModel

from ...graph import Entity, Graph
from ...page import Action, Item, Panel
from ...plugin import ActionHandler, CliHandler, Context, Plugin
from ...repositories import IcedStackRepository, WorkItemRepository
from . import actions, cli
from .config import NotesConfig
from .panels import Builder, entities
from .repository import MIGRATIONS


def _builder(ctx: Context[NotesConfig], graph: Graph) -> Builder:
    return Builder(
        ctx.now, graph, WorkItemRepository(ctx.db).all(), IcedStackRepository(ctx.db).all()
    )


def _handler(action: actions.NotesAction) -> ActionHandler:
    def run(ctx: Context[NotesConfig], graph: Graph, fields: dict[str, str]) -> str:
        return action(ctx.config, graph, fields)

    return run


class NotesPlugin(Plugin[NotesConfig, BaseModel]):
    name = "notes"
    title = "Notes"
    config_model = NotesConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    cli_help = "import the skill's markdown notes, or export them back"

    def entities(self, ctx: Context[NotesConfig], snapshot: BaseModel | None) -> list[Entity]:
        return entities(WorkItemRepository(ctx.db).all(), IcedStackRepository(ctx.db).all())

    def panels(
        self, ctx: Context[NotesConfig], snapshot: BaseModel | None, graph: Graph
    ) -> list[Panel]:
        builder = _builder(ctx, graph)
        return [builder.todos(), builder.iced()]

    def item_actions(self, ctx: Context[NotesConfig], item: Item, graph: Graph) -> list[Action]:
        builder = _builder(ctx, graph)
        return [a for a in (builder.put_on_ice(item), builder.add_todo(item)) if a]

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        return {name: _handler(action) for name, action in actions.ACTIONS.items()}
