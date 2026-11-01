"""Runs the plugins' collectors, stores their snapshots, and builds the dashboard from what's
stored."""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from pydantic import BaseModel

from . import panels, shell
from .config import Config
from .db import Database
from .domain import Model
from .graph import Graph
from .page import Action, Item, SourceStatus
from .plugin import ActionError, ActionResult, collect_into
from .prune import prune_if_due
from .registry import Enabled, build_graph, enabled
from .repositories import SnapshotRepository
from .shell import CommandError

log = logging.getLogger(__name__)

PERSON = "person"


class _PersonView(Model):
    """What core reads of a `person`: whether it's me."""

    id: str
    me: bool = False


def stored_graph(db: Database, plugins: list[Enabled]) -> tuple[Graph, dict[str, BaseModel | None]]:
    """The graph over what's stored, with the snapshots it was built from."""
    snapshots = {e.name: e.snapshot() for e in plugins}
    return build_graph(plugins, snapshots), snapshots


def _collect_all(
    db: Database, plugins: list[Enabled], now: datetime, collect: Callable[[Enabled], BaseModel]
) -> None:
    """Collect each plugin's snapshot with `collect`, in parallel, and keep it with the
    plugin's `store`."""
    with ThreadPoolExecutor(max_workers=max(1, len(plugins))) as pool:
        for e in plugins:
            pool.submit(
                collect_into,
                db,
                e.name,
                now,
                lambda e=e: collect(e),
                lambda snapshot, e=e: e.plugin.store(e.ctx, snapshot),
            )


def refresh(config: Config, db: Database, now: datetime | None = None) -> None:
    """Refresh every plugin. Each plugin fails on its own."""
    now = now or datetime.now(UTC)
    plugins = enabled(config, db, now)
    collecting = [e for e in plugins if e.plugin.snapshot_model is not None]
    first = [e for e in collecting if not e.plugin.collects_after_graph]
    after = [e for e in collecting if e.plugin.collects_after_graph]
    _collect_all(db, first, now, lambda e: e.plugin.collect(e.ctx))
    if shell.stopping():
        return
    graph, _ = stored_graph(db, plugins)
    if after:
        _collect_all(db, after, now, lambda e: e.plugin.collect_after(e.ctx, graph))
        if shell.stopping():
            return
        graph, _ = stored_graph(db, plugins)
    prune_if_due(db, now, lambda: _prune_plugins(plugins, graph))
    for e in plugins:
        try:
            e.plugin.after_collect(e.ctx, graph)
        except Exception:
            log.exception("%s: after_collect failed", e.name)


def _prune_plugins(plugins: list[Enabled], graph: Graph) -> list[str]:
    """Every plugin's daily prune, each failing on its own; what they did, by plugin."""
    notices: list[str] = []
    for e in plugins:
        try:
            notices += [f"{e.name}: {notice}" for notice in e.plugin.prune(e.ctx, graph)]
        except Exception:
            log.exception("%s: prune failed", e.name)
    return notices


def build_dashboard(config: Config, db: Database, now: datetime | None = None) -> panels.Dashboard:
    """The dashboard from whatever is stored; cheap, no network."""
    now = now or datetime.now(UTC)
    plugins = enabled(config, db, now)
    graph, snapshots = stored_graph(db, plugins)
    plugin_panels = [
        panels.PluginPanels(
            e.name, e.plugin.title, e.plugin.panels(e.ctx, snapshots[e.name], graph)
        )
        for e in plugins
    ]
    me = next((p for p in graph.view(PERSON, _PersonView) if p.me), None)
    sources = {
        source: SourceStatus(fetched_at=row.fetched_at, error=row.error)
        for source, row in SnapshotRepository(db).all().items()
    }

    prepared = [e.plugin.prepare_item_actions(e.ctx, graph) for e in plugins]

    def item_actions(item: Item) -> list[Action]:
        return [a for actions in prepared for a in actions(item)]

    def hidden(item: Item) -> bool:
        """About something hidden, unless it's about what hides it (an On ice item)."""
        if any(graph.hides(s.kind) for s in item.subjects):
            return False
        return any(graph.hidden(s) for s in item.subjects)

    return panels.build(now, plugin_panels, sources, me.id if me else "", item_actions, hidden)


def plugin_page(config: Config, db: Database, path: str, query: dict[str, list[str]]) -> str | None:
    """An enabled plugin's page at `path`, or None when there's no such page (or the plugin
    has nothing at that query)."""
    plugins = enabled(config, db, datetime.now(UTC))
    for e in plugins:
        if handler := e.plugin.pages().get(path):
            graph, _ = stored_graph(db, plugins)
            return handler(e.ctx, graph, query)
    return None


def run_action(config: Config, db: Database, name: str, fields: dict[str, str]) -> str:
    """Run a plugin's action, `<plugin>/<action>`, for its notice; raises `ActionError` when
    it can't."""
    return perform_action(config, db, name, fields).notice


def kept_item(config: Config, db: Database, kept: str) -> tuple[str, Item] | None:
    """The item an action asked to keep on the focus page, by its `KEPT` parameter, with the
    key of its panel."""
    plugin_name, _, key = kept.partition(":")
    plugins = enabled(config, db, datetime.now(UTC))
    e = next((e for e in plugins if e.name == plugin_name), None)
    if e is None:
        return None
    graph, _ = stored_graph(db, plugins)
    kept_item = e.plugin.kept_item(e.ctx, graph, key)
    if kept_item is None:
        return None
    panel, item = kept_item
    return panels.panel_key(e.name, panel), item


def perform_action(config: Config, db: Database, name: str, fields: dict[str, str]) -> ActionResult:
    """Run a plugin's action, `<plugin>/<action>`; raises `ActionError` when it can't."""
    plugin_name, _, action_name = name.partition("/")
    plugins = enabled(config, db, datetime.now(UTC))
    e = next((e for e in plugins if e.name == plugin_name), None)
    handler = e.plugin.actions().get(action_name) if e else None
    if e is None or handler is None:
        raise ActionError(f"unknown action {name}")
    graph, _ = stored_graph(db, plugins)
    try:
        result = handler(e.ctx, graph, fields)
    except KeyError as err:
        raise ActionError(f"missing field {err}") from err
    except CommandError as err:
        raise ActionError(str(err)) from err
    return result if isinstance(result, ActionResult) else ActionResult(result)
