"""The built-in plugins, and the enabled ones with their contexts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .config import Config
from .db import Database
from .graph import Entity, Graph
from .plugin import Context, Plugin
from .plugins.git.plugin import GitPlugin
from .plugins.github.plugin import GitHubPlugin
from .plugins.jira.plugin import JiraPlugin
from .plugins.notes.plugin import NotesPlugin
from .plugins.slack.plugin import SlackPlugin
from .repositories import KvRepository, SnapshotRepository

PLUGINS: list[Plugin[Any, Any]] = [
    GitHubPlugin(),
    GitPlugin(),
    JiraPlugin(),
    SlackPlugin(),
    NotesPlugin(),
]
"""Every built-in plugin, in page order. Each one's name is a field of `Config`."""


@dataclass(frozen=True)
class Enabled:
    """A plugin whose config section is there, with the context it runs in."""

    plugin: Plugin[Any, Any]
    ctx: Context[Any]

    @property
    def name(self) -> str:
        return self.plugin.name

    def snapshot(self) -> BaseModel | None:
        model = self.plugin.snapshot_model
        return SnapshotRepository(self.ctx.db).get(self.name, model) if model else None


def enabled(config: Config, db: Database, now: datetime) -> list[Enabled]:
    """The plugins with a config section, their migrations applied."""
    result: list[Enabled] = []
    for plugin in PLUGINS:
        section: BaseModel | None = getattr(config, plugin.name)
        if section is None:
            continue
        db.migrate(plugin.name, plugin.migrations)
        ctx = Context(
            config=section,
            email=config.email,
            teams=list(config.teams),
            prune_days=config.prune_days,
            now=now,
            db=db,
            kv=KvRepository(db, plugin.name),
        )
        result.append(Enabled(plugin, ctx))
    return result


def build_graph(plugins: list[Enabled], snapshots: dict[str, BaseModel | None]) -> Graph:
    """The graph of every plugin's entities."""
    entities: list[Entity] = []
    for e in plugins:
        entities += e.plugin.entities(e.ctx, snapshots.get(e.name))

    def url_for(kind: str, id: str) -> str | None:
        return next((url for e in plugins if (url := e.plugin.url_for(e.ctx, kind, id))), None)

    return Graph(entities, url_for)
