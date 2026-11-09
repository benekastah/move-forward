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

from . import (
    act_now,
    localtime,
    notifications,
    panels,
    people,
    priority,
    registry,
    shell,
    tags,
)
from .act_now import Feedback
from .config import Config
from .db import CORE, Database
from .graph import Graph, Ref
from .kinds import FAMILIES
from .notifications import Event, NotificationRepository
from .page import Item, ItemAdditions, Panel, SourceStatus, Surface
from .plugin import (
    MAX_SUBJECTS,
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
    Response,
    StoreRequest,
    Subjects,
    SubjectsRequest,
    collect_into,
)
from .prune import prune_if_due
from .registry import Enabled, build_graph, call, enabled
from .repositories import (
    ContactRepository,
    FeedbackRepository,
    KvRepository,
    LayoutRepository,
    PriorityRepository,
    SnapshotRepository,
    TagRepository,
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
    """The graph over what's stored, with the snapshots it was built from, and the tags I put on
    entities."""
    snapshots = {e.name: e.snapshot() for e in plugins}
    return build_graph(plugins, snapshots, TagRepository(db).all()), snapshots


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
    """How often a plugin refreshes on its own (`Plugin.refresh_seconds`); None for one that
    doesn't collect, or doesn't refresh on its own."""
    if not e.plugin.collects:
        return None
    return call(e.name, "refresh_seconds", lambda: e.plugin.refresh_seconds(e.ctx), None)


def to_collect(
    plugins: list[Enabled], only: set[str] | None = None
) -> tuple[list[Enabled], list[Enabled]]:
    """What a refresh of `only` (None: every plugin) collects: first the plugins that collect
    on their own, then those that collect after the graph, including any whose `requires`
    are among the first, unless it `watches` them too: then its `changed` decides, right
    after they refresh."""
    collecting = [e for e in plugins if e.plugin.collects]
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
    _dashboard_events(config, db, plugins, subscribed, now)
    prune_if_due(db, now, lambda: _prune_plugins(plugins, graph, db, now, config.prune_days))
    for e in plugins:
        if e.name not in collected and e.plugin.collects:
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


def publishers(graph: Graph, refs: list[Ref]) -> dict[str, list[Ref]]:
    """`refs` by the plugin that published each: a family's by whoever published that id, a
    plugin's own kind by that plugin. Refs the graph doesn't have are left out."""
    found: dict[str, list[Ref]] = {}
    for ref in dict.fromkeys(refs):
        entity = graph.get(ref.kind, ref.id)
        if entity is not None and entity.kind:
            found.setdefault(entity.kind.split(".", 1)[0], []).append(ref)
    return found


def refresh_subjects(
    config: Config, db: Database, refs: list[Ref], now: datetime | None = None
) -> set[str]:
    """Fetch these entities again through their plugins' `refresh_subjects`, `MAX_SUBJECTS`
    at a time, keeping what each returns as a refresh would (its events too); returns the
    plugins that couldn't, for a full refresh instead."""
    now = now or datetime.now(UTC)
    plugins = {e.name: e for e in enabled(config, db, now)}
    graph, _ = stored_graph(db, list(plugins.values()))
    subscribed = set(config.notifications.subscribe)
    fallback: set[str] = set()
    for name, mine in publishers(graph, refs).items():
        e = plugins[name]
        if not e.plugin.collects:
            continue
        for start in range(0, len(mine), MAX_SUBJECTS):
            result = _subjects(e, mine[start : start + MAX_SUBJECTS])
            if result is None:
                fallback.add(name)
                break
            try:
                _store(e, result.snapshot, subscribed, now, graph)
            except Exception:
                log.exception("%s: storing its subjects failed", name)
                fallback.add(name)
                break
    return fallback


def _subjects(e: Enabled, refs: list[Ref]) -> Subjects[BaseModel] | None:
    """The plugin's `refresh_subjects`, within its `subjects_timeout_seconds`; None when it
    can't, fails or takes longer (the thread is left to finish, as a collect's is)."""
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"subjects-{e.name}")
    future = pool.submit(
        lambda: e.plugin.refresh_subjects(SubjectsRequest(e.ctx, refs, e.snapshot()))
    )
    pool.shutdown(wait=False)
    try:
        return future.result(timeout=e.plugin.subjects_timeout_seconds)
    except FutureTimeout:
        log.warning("%s: refreshing %s subject(s) took too long", e.name, len(refs))
    except Exception:
        log.exception("%s: refreshing subjects failed", e.name)
    return None


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


def _dashboard_events(
    config: Config,
    db: Database,
    plugins: list[Enabled],
    subscribed: set[str],
    now: datetime,
) -> None:
    """Store the events about the dashboard itself, those I've subscribed to."""
    wanted = {k for k in notifications.DASHBOARD_KINDS if k in subscribed}
    if not wanted:
        return
    try:
        events = [
            e
            for e in notifications.dashboard_events(
                db, config.notifications, now, [p.name for p in plugins]
            )
            if notifications.qualified(notifications.DASHBOARD, e.kind) in wanted
        ]
        NotificationRepository(db).add(notifications.DASHBOARD, events, now)
    except Exception:
        log.exception("dashboard: finding events failed")


def _prune_plugins(
    plugins: list[Enabled], graph: Graph, db: Database, now: datetime, days: int
) -> list[str]:
    """Every plugin's daily prune, and core's (Act now's feedback that's over or older than
    `days`, finished work's priorities, gone entities' tags), each failing on its own; what
    they did, by plugin."""
    notices: list[str] = []
    if dropped := FeedbackRepository(db).prune(now, now - timedelta(days=days)):
        notices.append(f"act_now: forgot {dropped} snooze(s), dismissal(s) or pin(s)")
    try:  # it asks plugins for what they no longer hold, which can fail (a token gone bad)
        if cleared := priority.forget_finished(PriorityRepository(db), graph):
            notices.append(f"act_now: cleared {cleared} priority(ies) of finished work")
    except Exception:
        log.exception("act_now: forgetting finished work's priorities failed")
    try:
        if cleared := tags.forget_gone(TagRepository(db), graph):
            notices.append(f"tags: took my tags off {cleared} entity(ies) that are gone")
    except Exception:
        log.exception("tags: forgetting gone entities' tags failed")
    for e in plugins:
        try:
            notices += [f"{e.name}: {n}" for n in e.plugin.prune(PruneRequest(e.ctx, graph))]
        except Exception:
            log.exception("%s: prune failed", e.name)
    return notices


def _merged(first: ItemAdditions, then: ItemAdditions) -> ItemAdditions:
    """Both one's additions to an item and the other's, in that order."""
    return ItemAdditions(
        actions=[*first.actions, *then.actions],
        badges=[*first.badges, *then.badges],
        details=[*first.details, *then.details],
        links=[*first.links, *then.links],
    )


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
            lambda e=e, s=snapshot, own=own: e.plugin.act_now(
                ActNowRequest(e.ctx, s, graph, own, timedelta(days=config.act_now.due_warning_days))
            ),
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
        for source, row in SnapshotRepository(db).of([e.name for e in plugins]).items()
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

    contacts = people.Contacts(ContactRepository(db).all(), graph, config.people)
    tagging = tags.Tags(graph, TagRepository(db).all())

    def item_actions(item: Item, owner: str, surface: Surface) -> ItemAdditions:
        found = _merged(contacts.additions(item), tagging.additions(item))
        for e, add in prepared:
            req = ItemActionsRequest(e.ctx, item, graph, owner, surface)
            added = call(e.name, "item_actions", lambda add=add, req=req: add(req), found)
            if added is not found:
                found = _merged(found, added)
        return found

    def hidden(item: Item) -> bool:
        """About something hidden, unless it's about what hides it (an On ice item)."""
        if any(graph.hides(s.kind) for s in item.subjects):
            return False
        return any(graph.hidden(s) for s in item.subjects)

    layout = LayoutRepository(db).load()
    built = panels.build(
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
        _known_action(plugins),
        graph.hides,
        contacts.importance,
    )
    return built.model_copy(update={"problems": list(registry.DISCOVERY_ERRORS)})


def _failed(e: Enabled) -> Panel:
    """What stands in for a plugin's panels when building them failed."""
    return Panel(
        key="failed",
        title=f"{e.plugin.title} (failed)",
        description="Building this plugin's panels failed; the log says why.",
        items=[],
    )


def page_of(path: str) -> tuple[str, str]:
    """The plugin and the page key a path names: `/slack/ping` is slack's `ping`, `/slack`
    its index, `""`."""
    plugin, _, key = path.strip("/").partition("/")
    return plugin, key


def plugin_page(
    config: Config, db: Database, path: str, query: dict[str, list[str]]
) -> str | Response | None:
    """The enabled plugin's page at `path`, `/<plugin>/<key>`, or None when there's no such
    page (or the plugin has nothing at that query). Raises `PageFailed` when its handler
    raises."""
    name, key = page_of(path)
    plugins = enabled(config, db, datetime.now(UTC))
    e = next((e for e in plugins if e.name == name), None)
    handler = e.plugin.pages().get(key) if e else None
    if e is None or handler is None:
        return None
    graph, _ = stored_graph(db, plugins)
    try:
        return handler(PageRequest(e.ctx, graph, query, path))
    except Exception as err:
        log.exception("%s: page %s failed", e.name, path)
        raise PageFailed(path) from err


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
    """The priority buttons on items: set the priority (`level`, 1 important or 2 critical)
    of the entity, or clear it when there's no level."""
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


def _set_contact(db: Database, fields: dict[str, str], now: datetime) -> ActionResult:
    """Set type: what the person or domain `choice` is to me, the type typed as `input`."""
    try:
        who = people.contact_key(fields["choice"])
        contact_type = people.contact_type(fields["input"])
    except ValueError as err:
        raise ActionError(str(err)) from err
    ContactRepository(db).set(who, contact_type, now)
    return ActionResult(f"{who}: {contact_type}")


def _tag(db: Database, action: str, fields: dict[str, str], now: datetime) -> ActionResult:
    """Tag: put the tag typed as `input` on the entity; Untag: take `tag` off it."""
    ref = Ref(kind=fields["kind"], id=fields["id"])
    repo = TagRepository(db)
    try:
        tag = tags.tag_key(fields["tag" if action == "untag" else "input"])
    except ValueError as err:
        raise ActionError(str(err)) from err
    if action == "untag":
        return ActionResult(
            f"#{tag} taken off" if repo.remove(ref, tag) else f"#{tag} wasn't on it"
        )
    return ActionResult(f"tagged #{tag}" if repo.add(ref, tag, now) else f"already #{tag}")


def _members(fields: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """The item an Act now button's `fields` name, and those shown with it (`also`), as kind,
    id, task and fingerprint."""
    try:
        members = [(fields["kind"], fields["id"], fields["task"], fields["fingerprint"])]
        members += _MEMBERS.validate_json(fields.get("also") or "[]")
    except KeyError as err:
        raise ActionError(f"missing field {err}") from err
    except ValidationError as err:
        raise ActionError("malformed `also`") from err
    return members


def snooze_item(
    config: act_now.ActNowConfig,
    db: Database,
    fields: dict[str, str],
    now: datetime,
    duration: timedelta | None = None,
) -> datetime:
    """Snooze the item a Snooze button's `fields` name, and those shown with it, until each
    changes (its fingerprint) or `duration` passes (by default as long as `config` says for
    its kind of subject): the Snooze button, and `mfd snooze`. When the item's snooze ends."""
    feedback = FeedbackRepository(db)
    end: datetime | None = None
    for kind, id, task, fingerprint in _members(fields):
        until = now + duration if duration else config.snooze_until(kind, now)
        feedback.say(
            (kind, id, task), Feedback(action="snooze", fingerprint=fingerprint, until=until), now
        )
        end = end or until  # the item's own, the first member
    if end is None:
        raise ValueError("a Snooze button names the item it snoozes")
    return end


def _act_now_action(
    config: act_now.ActNowConfig, db: Database, action: str, fields: dict[str, str]
) -> ActionResult:
    """Act now's own buttons, on any surface: snooze an item (for as long as `config` says),
    dismiss it, bring it back (unsnooze), or pin or unpin it, by its main subject and task, and
    the items joined to it (`also`) alike; set or clear the priority of its main subject; tag
    it; and set or clear who someone is to me."""
    now = datetime.now(UTC)
    if action in ("priority", "contact", "contact_clear", "tag", "untag"):
        try:
            if action == "priority":
                return _set_priority(db, fields, now)
            if action in ("tag", "untag"):
                return _tag(db, action, fields, now)
            if action == "contact":
                return _set_contact(db, fields, now)
            try:
                who, kept = people.clear_label(fields["who"], ContactRepository(db).clear)
            except ValueError as err:
                raise ActionError(str(err)) from err
            return ActionResult(f"{who}: type cleared" if kept else f"{who} had no type")
        except KeyError as err:
            raise ActionError(f"missing field {err}") from err
    members = _members(fields)
    feedback = FeedbackRepository(db)
    if action == "opened":
        # What I did elsewhere isn't a choice to keep: it never replaces a pin or a snooze.
        said = feedback.all()
        for kind, id, task, f in members:
            before = said.get((kind, id, task))
            if before is None or before.action == "opened":
                until = now + act_now.OPENED_FOR
                feedback.say(
                    (kind, id, task), Feedback(action="opened", fingerprint=f, until=until), now
                )
        return ActionResult("opened")
    if action == "unpin":
        for kind, id, task, _ in members:
            feedback.forget((kind, id, task))
        return ActionResult("unpinned")
    if action == "unsnooze":  # a pin made since, from another page, stays
        for kind, id, task, _ in members:
            feedback.bring_back(kind, id, task, now)
        return ActionResult("back")
    if action == "snooze":
        until = localtime.day(snooze_item(config, db, fields, now))
        tomorrow = localtime.day(now) + timedelta(days=1)
        return ActionResult(
            "snoozed until " + ("tomorrow" if until == tomorrow else until.isoformat())
        )
    if action not in ("dismiss", "pin"):
        raise ActionError(f"unknown action {act_now.ACTIONS}/{action}")
    for kind, id, task, f in members:
        feedback.say((kind, id, task), Feedback(action=action, fingerprint=f), now)
    return ActionResult("dismissed" if action == "dismiss" else "pinned")


def _known_action(plugins: list[Enabled]) -> panels.KnownAction:
    """Whether a button's name is `<plugin>/<action>` of an enabled plugin's `actions`."""
    names = {
        f"{e.name}/{action}"
        for e in plugins
        for action in call(e.name, "actions", e.plugin.actions, {})
    }
    return names.__contains__


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
    opened = fields.get(act_now.OPENED_FIELD)
    fields = {k: v for k, v in fields.items() if k != act_now.OPENED_FIELD}
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
    if opened:
        _mark_opened(db, opened)
    return result


def _mark_opened(db: Database, opened: str) -> None:
    """Mark the Act now item a button was on (its `act_now_fields`) opened, dimmed until it
    changes; malformed fields mark nothing."""
    try:
        fields: dict[str, str] = json.loads(opened)
        _act_now_action(act_now.ActNowConfig(), db, "opened", fields)
    except (ValueError, ActionError, TypeError):
        log.debug("ignoring malformed opened fields: %r", opened)
