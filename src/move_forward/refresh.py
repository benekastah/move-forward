"""Runs the plugins' collectors, stores their snapshots, and builds the dashboard from what's
stored."""

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from . import act_now, localtime, notifications, panels, priority, shell, tls
from .act_now import Feedback
from .config import Config
from .db import CORE, Database
from .graph import Graph, Ref
from .kinds import FAMILIES
from .notifications import Event, NotificationRepository
from .page import Item, ItemAdditions, Panel, SourceStatus, Surface
from .plugin import (
    ActionError,
    ActionRequest,
    ActionResult,
    ActNowRequest,
    AfterCollectRequest,
    CollectAfterRequest,
    Collected,
    CollectRequest,
    EventsRequest,
    GraphEventsRequest,
    IdentityRequest,
    ItemActionsRequest,
    KeptItemRequest,
    LoadRequest,
    PageRequest,
    PanelsRequest,
    PrepareItemActionsRequest,
    PruneRequest,
    StoreRequest,
    collect_into,
)
from .prune import prune_if_due
from .registry import Enabled, build_graph, call, enabled
from .repositories import (
    FeedbackRepository,
    KvRepository,
    LayoutRepository,
    PriorityRepository,
    SnapshotRepository,
)
from .shell import CommandError

log = logging.getLogger(__name__)

MAX_PIECES = 50
"""How many times one refresh collects a plugin that keeps saying it has more."""

_collecting: set[str] = set()
"""The plugins whose `collect` is still running, maybe past its timeout: a thread can't be
stopped, so the next refresh leaves the plugin out until it returns."""
_collecting_lock = threading.Lock()


class PageFailed(Exception):
    """A plugin's page handler raised: that page answers 500, the rest of the dashboard
    stays up."""


def stored_graph(db: Database, plugins: list[Enabled]) -> tuple[Graph, dict[str, BaseModel | None]]:
    """The graph over what's stored, with the snapshots it was built from."""
    snapshots = {e.name: e.snapshot() for e in plugins}
    return build_graph(plugins, snapshots), snapshots


def _store(
    e: Enabled, snapshot: BaseModel, subscribed: set[str], now: datetime, previous: Graph
) -> None:
    """Keep a plugin's new snapshot and, when I've subscribed to any of its events, store
    those I subscribed to that changed since the last one. The first snapshot has nothing to
    compare with, so it makes none (core's kv remembers that it has been stored). A plugin
    that can't tell is logged, and never stops the refresh. `previous` is the graph from
    before this refresh."""
    wanted = _wants(e, subscribed)
    kv = KvRepository(e.ctx.db, CORE)
    key = f"stored:{e.name}"
    before = e.plugin.load(LoadRequest(e.ctx)) if wanted and kv.get(key) else None
    e.plugin.store(StoreRequest(e.ctx, snapshot))
    kv.set(key, "1")
    if before is None:
        return
    try:
        events = e.plugin.events(EventsRequest(e.ctx, before, snapshot, previous))
        _keep(e, events, subscribed, now)
    except Exception:
        log.exception("%s: finding events failed", e.name)


def _keep(e: Enabled, events: list[Event], subscribed: set[str], now: datetime) -> None:
    """Store a plugin's events of the kinds I've subscribed to."""
    wanted = [ev for ev in events if notifications.qualified(e.name, ev.kind) in subscribed]
    NotificationRepository(e.ctx.db).add(e.name, wanted, now)


def _collect_all(
    db: Database,
    plugins: list[Enabled],
    now: datetime,
    collect: Callable[[Enabled], BaseModel | Collected[BaseModel]],
    subscribed: set[str],
    previous: Graph,
) -> set[str]:
    """Collect each plugin's snapshot with `collect`, in parallel, and keep it with the
    plugin's `store`. `subscribed` are the kinds I want events of (see `notifications`).
    Each plugin has its `collect_timeout_seconds`: one that takes longer is recorded as
    failed, and the refresh goes on without it; one still running from an earlier refresh
    is left out. Returns the plugins that have more to collect (`Collected.more`)."""
    started = time.monotonic()
    more: set[str] = set()

    def piece(e: Enabled) -> BaseModel:
        result = collect(e)
        if isinstance(result, Collected):
            if result.more:
                more.add(e.name)
            return result.snapshot
        return result

    pool = ThreadPoolExecutor(max_workers=max(1, len(plugins)))
    running: list[tuple[Enabled, Future[None]]] = []
    for e in plugins:
        with _collecting_lock:
            if e.name in _collecting:
                log.warning("%s: still collecting from an earlier refresh; left out", e.name)
                continue
            _collecting.add(e.name)

        def run(e: Enabled = e) -> None:
            try:
                collect_into(
                    db,
                    e.name,
                    now,
                    lambda: piece(e),
                    lambda snapshot: _store(e, snapshot, subscribed, now, previous),
                )
            finally:
                with _collecting_lock:
                    _collecting.discard(e.name)

        running.append((e, pool.submit(run)))
    for e, future in running:
        limit = e.plugin.collect_timeout_seconds
        try:
            future.result(timeout=max(0.0, started + limit - time.monotonic()))
        except FutureTimeout:
            log.warning("%s: collect timed out after %ss", e.name, limit)
            SnapshotRepository(db).save_error(e.name, now, f"timed out after {limit}s")
            more.discard(e.name)
    pool.shutdown(wait=False)
    return more


def _wants(e: Enabled, subscribed: set[str]) -> bool:
    """Whether I've subscribed to any event the plugin makes."""
    return any(
        notifications.qualified(e.name, kind) in subscribed for kind in e.plugin.notification_kinds
    )


def refresh_seconds(e: Enabled) -> int | None:
    """How often a plugin refreshes on its own: its section's `refresh_seconds`. None for a
    plugin that doesn't collect, or has no such setting (one that collects after the plugins
    it requires may rely on being refreshed with them)."""
    if e.plugin.snapshot_model is None:
        return None
    seconds: object = getattr(e.ctx.config, "refresh_seconds", None)
    return seconds if isinstance(seconds, int) else None


def to_collect(
    plugins: list[Enabled], only: set[str] | None = None
) -> tuple[list[Enabled], list[Enabled]]:
    """What a refresh of `only` (None: every plugin) collects: first the plugins that collect
    on their own, then those that collect after the graph, including any whose `requires`
    are among the first, unless it `watches` them too: then its `changed` decides, right
    after they refresh."""
    collecting = [e for e in plugins if e.plugin.snapshot_model is not None]
    first = [
        e
        for e in collecting
        if not e.plugin.collects_after_graph and (only is None or e.name in only)
    ]
    names = {e.name for e in first}
    after = [
        e
        for e in collecting
        if e.plugin.collects_after_graph
        and (
            only is None
            or e.name in only
            or names.intersection(set(e.plugin.requires) - set(e.plugin.watches))
        )
    ]
    return first, after


def refresh(
    config: Config, db: Database, now: datetime | None = None, only: set[str] | None = None
) -> None:
    """Refresh every plugin, or `only` those (and the plugins that collect after them, e.g.
    git_stack after git). Each plugin fails on its own. A plugin with more to collect
    (`Collected.more`) is collected again, piece by piece, up to `MAX_PIECES` times. After
    collecting, the graph is built again, the plugins' events found against what held
    before, the daily prune runs if due, and `after_collect` runs for the plugins collected
    and those that collect nothing (they work from the others' data)."""
    now = now or datetime.now(UTC)
    plugins = enabled(config, db, now)
    previous, previous_snapshots = stored_graph(db, plugins)
    first, after = to_collect(plugins, only)
    collected = {e.name for e in first + after}
    subscribed = set(config.notifications.subscribe)

    def in_pieces(
        due: list[Enabled], collect: Callable[[Enabled], BaseModel | Collected[BaseModel]]
    ) -> None:
        more = _collect_all(db, due, now, collect, subscribed, previous)
        for _ in range(MAX_PIECES - 1):
            if not more or shell.stopping():
                return
            more = _collect_all(
                db, [e for e in due if e.name in more], now, collect, subscribed, previous
            )
        if more:
            log.warning("%s: still more to collect after %s pieces", ", ".join(more), MAX_PIECES)

    in_pieces(first, lambda e: e.plugin.collect(CollectRequest(e.ctx)))
    if shell.stopping():
        return
    graph, _ = stored_graph(db, plugins)
    if after:
        in_pieces(after, lambda e: e.plugin.collect_after(CollectAfterRequest(e.ctx, graph)))
        if shell.stopping():
            return
        graph, _ = stored_graph(db, plugins)
    _identify_kept(db, graph)
    snapshots = {e.name: e.snapshot() for e in plugins}
    _graph_events(plugins, graph, snapshots, previous, previous_snapshots, subscribed, now)
    _dashboard_events(config, db, subscribed, now)
    prune_if_due(db, now, lambda: _prune_plugins(plugins, graph, db, now, config.prune_days))
    for e in plugins:
        if e.name not in collected and e.plugin.snapshot_model is not None:
            continue
        try:
            e.plugin.after_collect(AfterCollectRequest(e.ctx, graph))
        except Exception:
            log.exception("%s: after_collect failed", e.name)


def _identify_kept(db: Database, graph: Graph) -> None:
    """Give what core keeps about a family's entities (priorities, Act now's feedback) the
    ids those entities have now: a ticket kept by its key, from before tickets' ids were their
    URLs, gets its URL once the graph can say it. Plugins do the same for their own tables,
    in `after_collect`. What nothing can identify is kept as it is."""
    families = list(FAMILIES)
    moved = PriorityRepository(db).rekey(families, graph.identify)
    moved += FeedbackRepository(db).rekey(families, graph.identify)
    if moved:
        log.info("gave %s kept row(s) their entities' ids", moved)


def _graph_events(
    plugins: list[Enabled],
    graph: Graph,
    snapshots: dict[str, BaseModel | None],
    previous: Graph,
    previous_snapshots: dict[str, BaseModel | None],
    subscribed: set[str],
    now: datetime,
) -> None:
    """Store the events plugins make from the graph (`Plugin.graph_events`), against what
    held before this refresh, for those I've subscribed to some of."""
    for e in plugins:
        if not _wants(e, subscribed):
            continue
        req = GraphEventsRequest(
            e.ctx, graph, snapshots.get(e.name), previous, previous_snapshots.get(e.name)
        )
        try:
            _keep(e, e.plugin.graph_events(req), subscribed, now)
        except Exception:
            log.exception("%s: finding events from the graph failed", e.name)


def _dashboard_events(config: Config, db: Database, subscribed: set[str], now: datetime) -> None:
    """Store the events about the dashboard itself, those I've subscribed to."""
    wanted = {k for k in notifications.DASHBOARD_KINDS if k in subscribed}
    if not wanted:
        return
    try:
        paths = tls.Paths.beside(config.db_path) if config.https else None
        events = [
            e
            for e in notifications.dashboard_events(db, config.notifications, paths, now)
            if notifications.qualified(notifications.DASHBOARD, e.kind) in wanted
        ]
        NotificationRepository(db).add(notifications.DASHBOARD, events, now)
    except Exception:
        log.exception("dashboard: finding events failed")


def _prune_plugins(
    plugins: list[Enabled], graph: Graph, db: Database, now: datetime, days: int
) -> list[str]:
    """Every plugin's daily prune, each failing on its own, and Act now's feedback that's over
    or older than `days`; what they did, by plugin."""
    notices: list[str] = []
    if dropped := FeedbackRepository(db).prune(now, now - timedelta(days=days)):
        notices.append(f"act_now: forgot {dropped} snooze(s), dismissal(s) or pin(s)")
    if cleared := priority.forget_finished(PriorityRepository(db), graph):
        notices.append(f"act_now: cleared {cleared} priority(ies) of finished work")
    for e in plugins:
        try:
            notices += [f"{e.name}: {n}" for n in e.plugin.prune(PruneRequest(e.ctx, graph))]
        except Exception:
            log.exception("%s: prune failed", e.name)
    return notices


def build_dashboard(
    config: Config, db: Database, now: datetime | None = None, surface: Surface = "dashboard"
) -> panels.Dashboard:
    """The dashboard from whatever is stored; cheap, no network. `surface` is where the
    plugins' panels are shown (the dashboard or focus mode), for what other plugins add to
    their items."""
    now = now or datetime.now(UTC)
    plugins = enabled(config, db, now)
    graph, snapshots = stored_graph(db, plugins)
    plugin_panels: list[panels.PluginPanels] = []
    for e in plugins:
        snapshot = snapshots[e.name]
        own = call(
            e.name,
            "panels",
            lambda e=e, s=snapshot: e.plugin.panels(PanelsRequest(e.ctx, s, graph)),
            None,
        )
        if own is None:
            plugin_panels.append(panels.PluginPanels(e.name, e.plugin.title, [_failed(e)], []))
            continue
        nominated = call(
            e.name,
            "act_now",
            lambda e=e, s=snapshot, own=own: e.plugin.act_now(ActNowRequest(e.ctx, s, graph, own)),
            [],
        )
        plugin_panels.append(panels.PluginPanels(e.name, e.plugin.title, own, nominated))
    identities = [
        found
        for e in plugins
        if (
            found := call(
                e.name,
                "identity",
                lambda e=e: e.plugin.identity(IdentityRequest(e.ctx, snapshots[e.name])),
                None,
            )
        )
    ]
    sources = {
        source: SourceStatus(fetched_at=row.fetched_at, error=row.error)
        for source, row in SnapshotRepository(db).all().items()
    }

    def nothing(req: ItemActionsRequest[Any]) -> ItemAdditions:
        return ItemAdditions()

    prepared = [
        (
            e,
            call(
                e.name,
                "prepare_item_actions",
                lambda e=e: e.plugin.prepare_item_actions(PrepareItemActionsRequest(e.ctx, graph)),
                nothing,
            ),
        )
        for e in plugins
    ]

    def item_actions(item: Item, owner: str, surface: Surface) -> ItemAdditions:
        found = ItemAdditions()
        for e, add in prepared:
            req = ItemActionsRequest(e.ctx, item, graph, owner, surface)
            added = call(e.name, "item_actions", lambda add=add, req=req: add(req), found)
            if added is not found:
                found = ItemAdditions(
                    actions=[*found.actions, *added.actions],
                    badges=[*found.badges, *added.badges],
                    details=[*found.details, *added.details],
                    links=[*found.links, *added.links],
                )
        return found

    def hidden(item: Item) -> bool:
        """About something hidden, unless it's about what hides it (an On ice item)."""
        if any(graph.hides(s.kind) for s in item.subjects):
            return False
        return any(graph.hidden(s) for s in item.subjects)

    layout = LayoutRepository(db).load()
    return panels.build(
        now,
        plugin_panels,
        sources,
        identities,
        item_actions,
        hidden,
        layout,
        config.act_now,
        FeedbackRepository(db).all(),
        graph.units,
        priority.level_of(PriorityRepository(db).all(), graph),
        surface,
    )


def _failed(e: Enabled) -> Panel:
    """What stands in for a plugin's panels when building them failed."""
    return Panel(
        key="failed",
        title=f"{e.plugin.title} (failed)",
        description="Building this plugin's panels failed; the log says why.",
        items=[],
    )


def plugin_page(config: Config, db: Database, path: str, query: dict[str, list[str]]) -> str | None:
    """An enabled plugin's page at `path`, or None when there's no such page (or the plugin
    has nothing at that query). Raises `PageFailed` when its handler raises."""
    plugins = enabled(config, db, datetime.now(UTC))
    for e in plugins:
        if handler := e.plugin.pages().get(path):
            graph, _ = stored_graph(db, plugins)
            try:
                return handler(PageRequest(e.ctx, graph, query, path))
            except Exception as err:
                log.exception("%s: page %s failed", e.name, path)
                raise PageFailed(path) from err
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
    kept_item = call(
        e.name, "kept_item", lambda: e.plugin.kept_item(KeptItemRequest(e.ctx, graph, key)), None
    )
    if kept_item is None:
        return None
    panel, item = kept_item
    return panels.panel_key(e.name, panel), item


_MEMBERS = TypeAdapter(
    Annotated[list[tuple[str, str, str, str]], Field(max_length=act_now.MAX_MEMBERS)]
)
"""An Act now button's `also`: the items joined to its own, as kind, id, task, fingerprint."""


def _set_priority(db: Database, fields: dict[str, str], now: datetime) -> ActionResult:
    """Act now's priority buttons: set the priority (`level`, 0 to 2) of the entity, or clear
    it when there's no level."""
    ref = Ref(kind=fields["kind"], id=fields["id"])
    priorities = PriorityRepository(db)
    if "level" not in fields:
        priorities.clear(ref)
        return ActionResult("priority cleared")
    try:
        level = int(fields["level"])
    except ValueError as err:
        raise ActionError("malformed `level`") from err
    if level not in act_now.PRIORITIES:
        raise ActionError(f"no priority {level}")
    priorities.set(ref, level, now)
    return ActionResult(f"priority: {act_now.PRIORITIES[level].lower()}")


def _act_now_action(
    config: act_now.ActNowConfig, db: Database, action: str, fields: dict[str, str]
) -> ActionResult:
    """Act now's own buttons: snooze an item (for as long as `config` says), dismiss it, or pin
    or unpin it, by its main subject and task, and the items joined to it (`also`) alike; and
    set or clear the priority of its main subject."""
    now = datetime.now(UTC)
    if action == "priority":
        try:
            return _set_priority(db, fields, now)
        except KeyError as err:
            raise ActionError(f"missing field {err}") from err
    try:
        members = [(fields["kind"], fields["id"], fields["task"], fields["fingerprint"])]
        members += _MEMBERS.validate_json(fields.get("also") or "[]")
    except KeyError as err:
        raise ActionError(f"missing field {err}") from err
    except ValidationError as err:
        raise ActionError("malformed `also`") from err
    feedback = FeedbackRepository(db)
    if action == "pending":
        # What I did elsewhere isn't a choice to keep: it never replaces a pin or a snooze.
        said = feedback.all()
        for kind, id, task, f in members:
            if (kind, id, task) not in said:
                until = now + act_now.PENDING
                feedback.say(
                    (kind, id, task), Feedback(action="pending", fingerprint=f, until=until), now
                )
        return ActionResult("hidden until it's fetched again")
    if action == "unpin":
        for kind, id, task, _ in members:
            feedback.forget((kind, id, task))
        return ActionResult("unpinned")
    if action == "snooze":
        said = [
            Feedback(action="snooze", fingerprint=f, until=config.snooze_until(kind, now))
            for kind, _, _, f in members
        ]
        until = localtime.day(said[0].until or now)
        tomorrow = localtime.day(now) + timedelta(days=1)
        result = "snoozed until " + ("tomorrow" if until == tomorrow else until.isoformat())
    elif action in ("dismiss", "pin"):
        said = [Feedback(action=action, fingerprint=f) for *_, f in members]
        result = "dismissed" if action == "dismiss" else "pinned"
    else:
        raise ActionError(f"unknown action {act_now.ACTIONS}/{action}")
    for (kind, id, task, _), one in zip(members, said, strict=True):
        feedback.say((kind, id, task), one, now)
    return ActionResult(result)


def perform_action(
    config: Config, db: Database, name: str, fields: dict[str, str], back: str | None = None
) -> ActionResult:
    """Run a plugin's action, `<plugin>/<action>`, or Act now's, `act_now/<action>`; raises
    `ActionError` when it can't. `back` is the page the button was on, when it wasn't the
    dashboard."""
    surface: Surface = "focus" if back and back.startswith("/focus") else "dashboard"
    plugin_name, _, action_name = name.partition("/")
    if plugin_name == act_now.ACTIONS:
        return _act_now_action(config.act_now, db, action_name, fields)
    pending = fields.get(act_now.PENDING_FIELD)
    fields = {k: v for k, v in fields.items() if k != act_now.PENDING_FIELD}
    plugins = enabled(config, db, datetime.now(UTC))
    e = next((e for e in plugins if e.name == plugin_name), None)
    handler = e.plugin.actions().get(action_name) if e else None
    if e is None or handler is None:
        raise ActionError(f"unknown action {name}")
    graph, _ = stored_graph(db, plugins)
    try:
        result = handler(ActionRequest(e.ctx, graph, fields, surface, back))
    except ActionError:
        raise
    except CommandError as err:
        raise ActionError(str(err)) from err
    except Exception as err:
        log.exception("%s failed", name)
        raise ActionError(f"{name} failed: {type(err).__name__}: {err}") from err
    result = result if isinstance(result, ActionResult) else ActionResult(result)
    if pending:
        _hide_until_fetched(db, pending)
    return result


def _hide_until_fetched(db: Database, pending: str) -> None:
    """Hide the Act now item a button was on (its `pending` fields) until its plugin has
    fetched again, to show what the button did; a malformed `pending` hides nothing."""
    try:
        fields: dict[str, str] = json.loads(pending)
        _act_now_action(act_now.ActNowConfig(), db, "pending", fields)
    except (ValueError, ActionError, TypeError):
        log.debug("ignoring malformed pending fields: %r", pending)
