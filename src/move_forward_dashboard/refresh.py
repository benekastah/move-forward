"""Runs the plugins' collectors, stores their snapshots, and builds the dashboard from what's
stored."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from pydantic import BaseModel

from . import notes, panels, shell
from .config import Config
from .db import Database
from .domain import TeamChannel
from .graph import Graph
from .plugin import ActionError, collect_into
from .prune import prune_if_due
from .registry import Enabled, build_graph, enabled
from .repositories import (
    IcedStackRepository,
    NotesImportRepository,
    PingRepository,
    ReviewerRepository,
    SnapshotRepository,
    WorkItemRepository,
)
from .shell import CommandError

log = logging.getLogger(__name__)

PERSON = "person"


def _graph(db: Database, plugins: list[Enabled]) -> tuple[Graph, dict[str, BaseModel | None]]:
    """The graph over what's stored, with the snapshots it was built from."""
    snapshots = {e.name: e.snapshot() for e in plugins}
    legacy = panels.entities(
        WorkItemRepository(db).all(), IcedStackRepository(db).all(), ReviewerRepository(db).users()
    )
    return build_graph(plugins, snapshots, legacy), snapshots


def _collect(db: Database, e: Enabled, now: datetime) -> None:
    collect_into(db, e.name, now, lambda: e.plugin.collect(e.ctx))


def refresh(config: Config, db: Database, now: datetime | None = None) -> None:
    """Import the notes and refresh every plugin. Each plugin fails on its own."""
    now = now or datetime.now(UTC)
    NotesImportRepository(db).replace(notes.load(config.notes_dir))
    plugins = enabled(config, db, now)
    collecting = [e for e in plugins if e.plugin.snapshot_model is not None]
    with ThreadPoolExecutor(max_workers=max(1, len(collecting))) as pool:
        for e in collecting:
            pool.submit(_collect, db, e, now)
    if shell.stopping():
        return
    prune_if_due(config, db, now)
    graph, _ = _graph(db, plugins)
    for e in plugins:
        try:
            e.plugin.after_collect(e.ctx, graph)
        except Exception:
            log.exception("%s: after_collect failed", e.name)


def _team_channels(config: Config, known: dict[str, TeamChannel]) -> dict[str, TeamChannel]:
    """The reviewer map's team channels, with `[slack.teams]` winning by name."""
    teams = dict(known)
    for name, channel in config.slack.teams.items():
        current = teams.get(name)
        teams[name] = TeamChannel(
            slug=name, channel_name=channel, channel_id=current.channel_id if current else None
        )
    return teams


def build_dashboard(config: Config, db: Database, now: datetime | None = None) -> panels.Dashboard:
    """The dashboard from whatever is stored; cheap, no network."""
    now = now or datetime.now(UTC)
    plugins = enabled(config, db, now)
    graph, snapshots = _graph(db, plugins)
    plugin_panels = [
        panel for e in plugins for panel in e.plugin.panels(e.ctx, snapshots[e.name], graph)
    ]
    me = next((p for p in graph.of_kind(PERSON) if p.attrs.get("me")), None)
    inputs = panels.Inputs(
        now=now,
        graph=graph,
        work_items=WorkItemRepository(db).all(),
        iced_stacks=IcedStackRepository(db).all(),
        users=ReviewerRepository(db).users(),
        teams=_team_channels(config, ReviewerRepository(db).teams()),
        pings=PingRepository(db).all(),
        slack=config.slack,
    )
    sources = {
        source: panels.SourceStatus(fetched_at=row.fetched_at, error=row.error)
        for source, row in SnapshotRepository(db).all().items()
    }
    return panels.build(inputs, plugin_panels, sources, me.ref.id if me else "")


def run_action(config: Config, db: Database, name: str, fields: dict[str, str]) -> str:
    """Run a plugin's action, `<plugin>/<action>`; raises `ActionError` when it can't."""
    plugin_name, _, action_name = name.partition("/")
    plugins = enabled(config, db, datetime.now(UTC))
    e = next((e for e in plugins if e.name == plugin_name), None)
    handler = e.plugin.actions().get(action_name) if e else None
    if e is None or handler is None:
        raise ActionError(f"unknown action {name}")
    graph, _ = _graph(db, plugins)
    try:
        return handler(e.ctx, graph, fields)
    except KeyError as err:
        raise ActionError(f"missing field {err}") from err
    except CommandError as err:
        raise ActionError(str(err)) from err
