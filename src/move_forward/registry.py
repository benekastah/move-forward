"""The built-in plugins, and the enabled ones with their contexts."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from .db import Database
from .graph import Entity, Graph
from .plugin import Context, Plugin
from .plugins.git.plugin import GitPlugin
from .plugins.git_stack.plugin import GitStackPlugin
from .plugins.github.plugin import GitHubPlugin
from .plugins.jira.plugin import JiraPlugin
from .plugins.notes.plugin import NotesPlugin
from .plugins.slack.plugin import SlackPlugin
from .repositories import KvRepository

if TYPE_CHECKING:
    # Only for annotations: `Config` checks `unmet_requirements` as it loads.
    from .config import Config

log = logging.getLogger(__name__)

PLUGINS: list[Plugin[Any, Any]] = [
    GitHubPlugin(),
    GitPlugin(),
    GitStackPlugin(),
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
        return self.plugin.load(self.ctx)


def enabled_names(config: "Config") -> list[str]:
    """The plugins the config enables, in page order, without touching the database."""
    return [p.name for p in PLUGINS if getattr(config, p.name) is not None]


def unmet_requirements(config: "Config") -> list[str]:
    """What the enabled plugins need that the config doesn't enable, e.g. "[git_stack] needs
    [git]"."""
    names = enabled_names(config)
    return [
        f"[{p.name}] needs [{required}]"
        for p in PLUGINS
        if p.name in names
        for required in p.requires
        if required not in names
    ]


def migrate(config: "Config", db: Database) -> None:
    """Apply the enabled plugins' migrations. Once, when `mfd` opens the database."""
    for plugin in PLUGINS:
        if getattr(config, plugin.name) is not None:
            db.migrate(plugin.name, plugin.migrations)


def enabled(config: "Config", db: Database, now: datetime) -> list[Enabled]:
    """The plugins with a config section, with their contexts. Their tables are there once
    `migrate` has run."""
    result: list[Enabled] = []
    for plugin in PLUGINS:
        section: BaseModel | None = getattr(config, plugin.name)
        if section is None:
            continue
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

    def fetch(kind: str, ids: list[str]) -> list[Entity]:
        """Each plugin's answer; one that fails is logged and left out, so its entities just
        aren't known (the prune keeps what it can't check)."""
        found: list[Entity] = []
        for e in plugins:
            try:
                found += e.plugin.fetch(e.ctx, kind, ids)
            except Exception:
                log.exception("%s: fetching %s failed", e.name, kind)
        return found

    return Graph(entities, url_for, fetch)
