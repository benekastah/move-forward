"""The built-in plugins, and the enabled ones with their contexts."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from . import act_now, notifications, tls
from .db import CORE, OWNER_RE, Database
from .graph import Entity, Graph, KindSpec, Ref
from .plugin import (
    Context,
    EntitiesRequest,
    FetchRequest,
    LinkingRequest,
    LoadRequest,
    Plugin,
    SourceError,
    UrlRequest,
)
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

LOG_AGAIN_SECONDS = 600
"""How long a plugin hook's failure isn't logged again, so a broken hook called on every
page doesn't fill the log."""
_last_logged: dict[tuple[str, str], float] = {}
_last_logged_lock = threading.Lock()

PLUGINS: list[Plugin[Any, Any]] = [
    GitHubPlugin(),
    GitPlugin(),
    GitStackPlugin(),
    JiraPlugin(),
    SlackPlugin(),
    NotesPlugin(),
]
"""Every built-in plugin. Each one's name is a field of `Config`."""


def check_names(plugins: list[Plugin[Any, Any]]) -> None:
    """Raise unless every plugin's name is its own and fit to be one. A name is the plugin's
    config section, its kv namespace and migrations' owner, its tables' prefix and the
    first part of its actions' paths, so two plugins sharing one would share all of those;
    and it mustn't be core's (`core`) or Act now's (`act_now`), which use the same places."""
    names = [p.name for p in plugins]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        raise RuntimeError(f"two plugins are named {', '.join(duplicates)}")
    for name in names:
        if not OWNER_RE.match(name):
            raise RuntimeError(f"plugin name {name!r}: use lower-case letters, digits and _")
        if name in (CORE, act_now.ACTIONS):
            raise RuntimeError(f"plugin name {name!r} is core's")


check_names(PLUGINS)


@dataclass(frozen=True)
class Enabled:
    """A plugin whose config section is there, with the context it runs in."""

    plugin: Plugin[Any, Any]
    ctx: Context[Any]

    @property
    def name(self) -> str:
        return self.plugin.name

    def snapshot(self) -> BaseModel | None:
        """What the plugin last stored; None when it has nothing, or its `load` fails."""
        return call(self.name, "load", lambda: self.plugin.load(LoadRequest(self.ctx)), None)


def call[T](name: str, hook: str, fn: Callable[[], T], default: T) -> T:
    """`fn()`, a plugin's hook; `default` when it raises, so one plugin's bug can't take the
    page or another plugin's refresh down. The failure is logged, at most once per plugin and
    hook every `LOG_AGAIN_SECONDS`."""
    try:
        return fn()
    except Exception:
        now = time.monotonic()
        with _last_logged_lock:
            last = _last_logged.get((name, hook))
            due = last is None or now - last >= LOG_AGAIN_SECONDS
            if due:
                _last_logged[(name, hook)] = now
        if due:
            log.exception("%s: %s failed", name, hook)
        return default


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


def notification_kinds(config: "Config | None" = None) -> dict[str, str]:
    """Every kind of event I can subscribe to, with what it says: the dashboard's, and those
    of the plugins `config` enables (every plugin's without one)."""
    names = None if config is None else set(enabled_names(config))
    return notifications.available(
        {p.name: p.notification_kinds for p in PLUGINS if names is None or p.name in names}
    )


def notification_kinds_of(plugin_names: list[str]) -> dict[str, str]:
    """`notification_kinds` for the plugins named, e.g. those `mfd init` is setting up."""
    return notifications.available(
        {p.name: p.notification_kinds for p in PLUGINS if p.name in plugin_names}
    )


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
            dashboard_url=f"{tls.scheme(config.https, config.db_path)}://localhost:{config.port}",
            name=plugin.name,
            data_root=Path(config.db_path).parent,
        )
        result.append(Enabled(plugin, ctx))
    return result


def build_graph(plugins: list[Enabled], snapshots: dict[str, BaseModel | None]) -> Graph:
    """The graph of every plugin's entities. A plugin publishes only the kinds it declares,
    and owns them unless an earlier plugin declared them first; each entity must fit its
    kind's model, and be the only one of its ref. One that isn't is a plugin bug: it's
    logged and left out, and the page goes on without it. So is a plugin whose `entities`,
    `url_for` or `linking_patterns` raises: the graph is built without its answer."""
    owners: dict[str, str] = {}
    for e in plugins:
        for kind in e.plugin.kinds:
            owner = owners.setdefault(kind, e.name)
            if owner != e.name:
                log.error(
                    "%s and %s both declare %s; %s's are left out", owner, e.name, kind, e.name
                )
    entities: list[Entity] = []
    seen: set[Ref] = set()
    for e in plugins:
        published: list[Entity] = call(
            e.name,
            "entities",
            lambda e=e: e.plugin.entities(EntitiesRequest(e.ctx, snapshots.get(e.name))),
            [],
        )
        for entity in published:
            ref = entity.ref
            if owners.get(ref.kind) != e.name:
                log.error("%s published %s %s, a kind it doesn't own", e.name, ref.kind, ref.id)
                continue
            if ref in seen:
                log.error("%s published %s %s twice", e.name, ref.kind, ref.id)
                continue
            try:
                e.plugin.kinds[ref.kind].attrs.model_validate(entity.attrs)
            except ValidationError as error:
                log.error("%s published a bad %s %s: %s", e.name, ref.kind, ref.id, error)
                continue
            seen.add(ref)
            entities.append(entity)

    def url_for(kind: str, id: str) -> str | None:
        for e in plugins:
            url: str | None = call(
                e.name, "url_for", lambda e=e: e.plugin.url_for(UrlRequest(e.ctx, kind, id)), None
            )
            if url:
                return url
        return None

    def fetch(kind: str, ids: list[str]) -> list[Entity]:
        """Each plugin's answer; one that fails is logged and left out, so its entities just
        aren't known (the prune keeps what it can't check)."""
        found: list[Entity] = []
        for e in plugins:
            try:
                found += e.plugin.fetch(FetchRequest(e.ctx, kind, ids))
            except SourceError:
                raise
            except Exception:
                log.exception("%s: fetching %s failed", e.name, kind)
        return found

    linking: dict[str, str | None] = {}
    for e in plugins:
        patterns: dict[str, str | None] = call(
            e.name,
            "linking_patterns",
            lambda e=e: e.plugin.linking_patterns(LinkingRequest(e.ctx)),
            {},
        )
        linking.update(patterns)
    specs: dict[str, KindSpec] = {
        kind: spec
        for e in plugins
        for kind, spec in e.plugin.kinds.items()
        if owners.get(kind) == e.name
    }
    hiding = [kind for kind, spec in specs.items() if spec.hides]
    return Graph(entities, url_for, fetch, linking, hiding, specs)
