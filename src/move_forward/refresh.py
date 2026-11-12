"""Runs the plugins' collectors, stores their snapshots, and builds the dashboard from what's
stored."""

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

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
from .change_checks import ChangeChecks, Taken, said
from .config import Config
from .db import CORE, Database
from .domain import Model
from .graph import Graph, Ref, publisher
from .kinds import FAMILIES, PERSON
from .notifications import Event, NotificationRepository
from .page import Identity, Item, ItemAdditions, Panel, SourceStatus, Surface
from .plugin import (
    ActionError,
    ActionResult,
    Form,
    InputRequest,
    ItemRequest,
    Page,
    Previous,
    RefreshRequest,
    Request,
    Response,
    adds_nothing,
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
    ProblemRepository,
    SnapshotRepository,
    TagRepository,
)
from .shell import CommandError

log = logging.getLogger(__name__)

COLLECT_TIMEOUT_SECONDS = 300
"""How long a plugin's `collect` may take. Past it, the refresh records
the plugin as failed and goes on; the plugin is left out of refreshes until its collect
returns (a thread can't be stopped). A refresh that collects a watcher again (`_settle`) can
take twice this."""

_collecting: set[str] = set()
"""The plugins whose `collect` is still running, maybe past its timeout: a thread can't be
stopped, so the next refresh leaves the plugin out until it returns."""
_collecting_lock = threading.Lock()


class PageFailed(Exception):
    """A plugin's page handler raised: that page answers 500, the rest of the dashboard
    stays up."""


def stored_graph(db: Database, plugins: list[Enabled]) -> tuple[Graph, dict[str, BaseModel | None]]:
    """The graph over what's stored, with the snapshots it was built from, and the tags I put on
    entities or took off them."""
    snapshots = {e.name: e.snapshot() for e in plugins}
    repo = TagRepository(db)
    return build_graph(plugins, snapshots, repo.all(), repo.taken_off()), snapshots


STORED = "stored:"
"""Core's kv key, `stored:<plugin>`, saying the plugin has stored a snapshot (until it has,
its events have nothing to compare with), and when it last did, in ISO 8601 (`1`, from before
the time was kept, says only that it has)."""


def store_snapshot(e: Enabled, snapshot: BaseModel) -> None:
    """Keep a plugin's new snapshot, and remember that it has one, and when."""
    e.kept().save(snapshot)
    KvRepository(e.ctx.db, CORE).set(f"{STORED}{e.name}", e.ctx.now.isoformat())


def _stored_at(value: str) -> datetime | None:
    """When a `STORED` value says the plugin last stored; None for one from before the time was
    kept."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Before:
    """What held before a refresh, for its events to compare with."""

    graph: Graph
    snapshots: dict[str, BaseModel | None]
    """Each plugin's snapshot; None for one that had never stored one."""
    stored_at: dict[str, datetime]
    """When the refresh that last stored each plugin's snapshot began (a refresh or a refresh of
    subjects); one
    that never did (or did only before the time was kept) isn't here. A refresh that failed
    stored nothing, so it doesn't count."""

    def previous(self, name: str) -> Previous[BaseModel]:
        """What held before for the plugin `name`, as its `events` gets it."""
        return Previous(
            self.stored_at.get(name), snapshot=self.snapshots.get(name), graph=self.graph
        )


def before_refresh(db: Database, plugins: list[Enabled]) -> Before:
    """What's stored now, as a refresh about to start compares its events with."""
    graph, snapshots = stored_graph(db, plugins)
    kv = KvRepository(db, CORE)
    stored = {name: value for name in snapshots if (value := kv.get(f"{STORED}{name}"))}
    return Before(
        graph,
        {name: s if name in stored else None for name, s in snapshots.items()},
        {name: at for name, value in stored.items() if (at := _stored_at(value)) is not None},
    )


def changes_of(
    e: Enabled, previous: Previous[BaseModel], after: BaseModel | None, graph: Graph
) -> list[Event]:
    """The events a plugin's `events` makes of a refresh, subscribed to or not, from what held
    `previous`ly and its snapshot `after` and the graph now; none while it has no snapshot to
    compare with."""
    if previous.snapshot is None or after is None:
        return []
    return e.plugin.events(Request(e.ctx, graph, after, previous))


def newly_shown_of(e: Enabled, after: BaseModel | None, graph: Graph) -> list[Event]:
    """The events of a plugin's items newly shown with a notification (`Item.notify`),
    subscribed to or not, by `notifications.newly_shown`, which keeps what its panels show now
    for the next refresh. Its panels are built whether or not it raises any kind this way."""
    panels = e.plugin.panels(Request(e.ctx, graph, after))
    return notifications.newly_shown(e.ctx.db, e.name, panels)


def events_of(
    e: Enabled, previous: Previous[BaseModel], after: BaseModel | None, graph: Graph
) -> list[Event]:
    """Every event a plugin makes of a refresh, subscribed to or not: `changes_of`, then
    `newly_shown_of`."""
    return [*changes_of(e, previous, after, graph), *newly_shown_of(e, after, graph)]


def _events(
    db: Database,
    plugins: list[Enabled],
    before: Before,
    graph: Graph,
    snapshots: dict[str, BaseModel | None],
    subscribed: set[str],
    now: datetime,
) -> None:
    """Store the events of a refresh, of the kinds I've subscribed to, for every plugin I've
    subscribed to some of: what changed since `before`, to the `graph` and `snapshots` now.
    A plugin's `events` and its panels' items are each asked on their own, so one failing
    loses only its own events: it's logged, and never stops the refresh. What the panels of
    the others showed is forgotten."""
    wanting = [e for e in plugins if _wants(e, subscribed)]
    notifications.forget_shown(db, [e.name for e in wanting])
    for e in wanting:
        after = snapshots.get(e.name)
        try:
            found = changes_of(e, before.previous(e.name), after, graph)
            _keep(e, found, subscribed, now)
        except Exception:
            log.exception("%s: finding what changed failed", e.name)
        try:
            _keep(e, newly_shown_of(e, after, graph), subscribed, now)
        except Exception:
            log.exception("%s: finding items newly shown failed", e.name)


def _keep(e: Enabled, events: list[Event], subscribed: set[str], now: datetime) -> None:
    """Store a plugin's events of the kinds I've subscribed to."""
    wanted = [ev for ev in events if notifications.qualified(e.name, ev.kind) in subscribed]
    NotificationRepository(e.ctx.db).add(e.name, wanted, now)


def _collect_all(
    db: Database,
    plugins: list[Enabled],
    now: datetime,
    collect: Callable[[Enabled, Taken], BaseModel],
    checks: ChangeChecks,
    graph: Graph,
    asking: bool,
    asked: Mapping[str, frozenset[str]] | None = None,
    named: set[str] | None = None,
) -> set[str]:
    """Collect each plugin's snapshot with `collect`, in parallel, from `graph`, given what
    moved since its last collect and the ids of its entities `asked` for (`ChangeChecks.take`,
    asking its check first when `asking`; a plugin not `named`, None meaning all, was asked
    for them alone, so they narrow its collect), and keep it where the plugin keeps it
    (`Plugin.snapshots`), which settles what it was given and lets the tags I took off what it
    publishes come back (`TagRepository.put_back`): it has found them again, or not. Each
    plugin has
    `COLLECT_TIMEOUT_SECONDS`: one that takes longer is recorded as failed, and the refresh
    goes on without it; one still running from an earlier collect is left out. Returns the
    plugins whose collect it started."""
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=max(1, len(plugins)))
    running: list[tuple[Enabled, Future[None]]] = []
    for e in plugins:
        with _collecting_lock:
            if e.name in _collecting:
                log.warning("%s: still collecting from an earlier collect; left out", e.name)
                continue
            _collecting.add(e.name)

        def run(e: Enabled = e) -> None:
            try:
                taken: Taken | None = None

                def collector() -> BaseModel:  # taking within, so its failure is the plugin's
                    nonlocal taken
                    taken = checks.take(
                        e,
                        graph if asking else None,
                        (asked or {}).get(e.name),
                        named is None or e.name in named,
                    )
                    return collect(e, taken)

                def store(snapshot: BaseModel) -> None:
                    store_snapshot(e, snapshot)
                    assert taken is not None  # the collector took it
                    checks.stored(e, taken)
                    TagRepository(db).put_back(e.name, now)

                collect_into(db, e.name, now, collector, store)
            finally:
                with _collecting_lock:
                    _collecting.discard(e.name)

        running.append((e, pool.submit(run)))
    for e, future in running:
        limit = COLLECT_TIMEOUT_SECONDS
        try:
            future.result(timeout=max(0.0, started + limit - time.monotonic()))
        except FutureTimeout:
            log.warning("%s: collect timed out after %ss", e.name, limit)
            SnapshotRepository(db).save_error(e.name, now, f"timed out after {limit}s")
    pool.shutdown(wait=False)
    return {e.name for e, _ in running}


def _wants(e: Enabled, subscribed: set[str]) -> bool:
    """Whether I've subscribed to any event the plugin makes."""
    return any(
        notifications.qualified(e.name, kind) in subscribed for kind in e.plugin.notification_kinds
    )


def to_collect(plugins: list[Enabled], only: set[str] | None = None) -> list[Enabled]:
    """What a refresh of `only` (None: every plugin) collects: the plugins that collect."""
    return [e for e in plugins if e.plugin.collects and (only is None or e.name in only)]


def _collector(graph: Graph) -> Callable[[Enabled, Taken], BaseModel]:
    """Collects a plugin from `graph`, given what `ChangeChecks.take` took."""

    def collect(e: Enabled, taken: Taken) -> BaseModel:
        return e.plugin.collect(RefreshRequest(e.ctx, graph, taken.full, taken.subjects))

    return collect


def _settle(
    db: Database,
    plugins: list[Enabled],
    collected: set[str],
    now: datetime,
    checks: ChangeChecks,
    before: Graph,
    graph: Graph,
) -> set[str]:
    """Ask each plugin that collects and `watches` one of those `collected` this refresh
    whether it changed, with the `graph` they stored (and, for one not checked since `checks`
    started, `before` as the graph to compare with); collect those that did once more, from
    `graph`, given what their checks said moved. A watcher collected this refresh worked from
    `before`, without what the plugins it watches just found (git_stack, git's new repos):
    this brings it up to date in the same refresh. Once, not until nothing moves. Returns the
    plugins whose collect it started: not one still collecting, its first collect past its
    timeout."""
    watching = [
        e for e in plugins if e.plugin.collects and collected.intersection(e.plugin.watches)
    ]
    moved = {e.name: why for e in watching if (why := checks.moved(e, graph, before)) is not None}
    if not moved:
        return set()
    again = [e for e in watching if e.name in moved]
    watched = sorted({name for e in again for name in e.plugin.watches} & collected)
    log.info("changed with %s: %s; collecting", ", ".join(watched), said(moved))
    return _collect_all(db, again, now, _collector(graph), checks, graph, asking=False)


def refresh(
    config: Config,
    db: Database,
    now: datetime | None = None,
    only: set[str] | None = None,
    checks: ChangeChecks | None = None,
    asked: Mapping[str, frozenset[str]] | None = None,
) -> set[str]:
    """Refresh every plugin, or `only` those and the plugins `asked` names, and return those
    whose collect started (not one still running past its timeout). Each plugin fails on its
    own, its outcome recorded as its status. Every plugin due collects at once, in parallel,
    from the graph as stored before the refresh. Each collect is given what moved since the
    plugin's last (`ChangeChecks.take`) and the ids of its entities `asked` for (see
    `asked_of`): a plugin that isn't one of `only` is refreshed for them alone, its collect
    narrowed to them; one that is (or every plugin, with no `only`) is collected as it would
    have been, given them too. With the server's `checks`, a plugin with a change check is asked
    it as its collect starts. Then the plugins watching those collected are asked whether they
    changed with what was stored, and collected once more if they did (`_settle`), so a refresh
    can take two `COLLECT_TIMEOUT_SECONDS` (and the server hold its refresh lock that long).
    After collecting, the graph is built again, the plugins' events found against what held
    before, the daily prune runs if due, and `after_collect` runs for the plugins collected and
    those that collect nothing (they work from the others' data). When the dashboard is
    stopping, it collects no more, but still finds the events of what it stored, and ends there,
    as long as it gets that far: the server runs it in a daemon thread, which its own shutdown
    may freeze first, and then that refresh's events are lost (the next start compares with what
    it stored). Run outside that thread (`mfd refresh`, a test), it always gets that far."""
    now = now or datetime.now(UTC)
    asked = asked or {}
    named = only
    only = None if only is None else only | set(asked)
    plugins = enabled(config, db, now)
    before = before_refresh(db, plugins)
    due = to_collect(plugins, only)
    asking = checks is not None
    checks = checks or ChangeChecks()
    collect = _collector(before.graph)
    collected = _collect_all(db, due, now, collect, checks, before.graph, asking, asked, named)
    graph, snapshots = stored_graph(db, plugins)
    if not shell.stopping() and (
        settled := _settle(db, plugins, collected, now, checks, before.graph, graph)
    ):
        collected |= settled
        graph, snapshots = stored_graph(db, plugins)
    _identify_kept(db, graph)
    _events(db, plugins, before, graph, snapshots, set(config.notifications.subscribe), now)
    if shell.stopping():  # what was stored is told above, if the thread got that far
        return collected
    _dashboard_events(config, db, plugins, set(config.notifications.subscribe), now)
    prune_if_due(
        db, now, lambda: _prune_plugins(plugins, graph, snapshots, db, now, config.prune_days)
    )
    for e in plugins:
        if e.name not in collected and e.plugin.collects:
            continue
        try:
            e.plugin.after_collect(Request(e.ctx, graph, snapshots.get(e.name)))
        except Exception:
            log.exception("%s: after_collect failed", e.name)
    return collected


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
    """`refs` by the plugin that published each (see `move_forward.graph.publisher`). Refs the
    graph doesn't have are left out."""
    found: dict[str, list[Ref]] = {}
    for ref in dict.fromkeys(refs):
        if (name := publisher(graph, ref)) is not None:
            found.setdefault(name, []).append(ref)
    return found


def asked_of(config: Config, db: Database, refs: list[Ref]) -> dict[str, frozenset[str]]:
    """The ids of `refs` (entities asked to be fetched again: what an action touched, what I
    opened or asked for with ↻ or `mfd refresh --subject`), by the plugin that publishes each,
    for `refresh`'s `asked`. Refs the graph doesn't have, or whose publisher collects nothing,
    are left out."""
    plugins = enabled(config, db, datetime.now(UTC))
    graph, _ = stored_graph(db, plugins)
    collecting = {e.name for e in plugins if e.plugin.collects}
    return {
        name: frozenset(ref.id for ref in mine)
        for name, mine in publishers(graph, refs).items()
        if name in collecting
    }


def refresh_subjects(
    config: Config, db: Database, refs: list[Ref], now: datetime | None = None
) -> None:
    """Fetch these entities again: a refresh of just the plugins that publish them, each
    collect given their ids (see `asked_of`), recorded and told as any refresh."""
    if asked := asked_of(config, db, refs):
        refresh(config, db, now, set(), None, asked)


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
    plugins: list[Enabled],
    graph: Graph,
    snapshots: dict[str, BaseModel | None],
    db: Database,
    now: datetime,
    days: int,
) -> list[str]:
    """Every plugin's daily prune, and core's (Act now's feedback that's over or older than
    `days`, problems no plugin has reported for `days`, finished work's priorities, gone
    entities' tags and untags), each failing on its own; what they did, by plugin."""
    notices: list[str] = []
    if dropped := FeedbackRepository(db).prune(now, now - timedelta(days=days)):
        notices.append(f"act_now: forgot {dropped} snooze(s) or pin(s)")
    if dropped := ProblemRepository(db).prune(now - timedelta(days=days)):
        notices.append(f"problems: forgot {dropped} not reported for {days} days")
    try:  # it asks plugins for what they no longer hold, which can fail (a token gone bad)
        if cleared := priority.forget_finished(PriorityRepository(db), graph):
            notices.append(f"act_now: cleared {cleared} priority(ies) of finished work")
    except Exception:
        log.exception("act_now: forgetting finished work's priorities failed")
    try:
        if cleared := tags.forget_gone(TagRepository(db), graph):
            notices.append(f"tags: forgot what I tagged or untagged on {cleared} gone entity(ies)")
    except Exception:
        log.exception("tags: forgetting gone entities' tags failed")
    for e in plugins:
        try:
            notices += [
                f"{e.name}: {n}"
                for n in e.plugin.prune(Request(e.ctx, graph, snapshots.get(e.name)))
            ]
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


def _plugin_panels(
    plugins: list[Enabled],
    graph: Graph,
    snapshots: dict[str, BaseModel | None],
) -> list[panels.PluginPanels]:
    """Each plugin's panels and its nominations for Act now, from its stored snapshot."""
    plugin_panels: list[panels.PluginPanels] = []
    for e in plugins:
        snapshot = snapshots[e.name]
        own = call(
            e.name,
            "panels",
            lambda e=e, s=snapshot: e.plugin.panels(Request(e.ctx, graph, s)),
            None,
        )
        if own is None:
            plugin_panels.append(panels.PluginPanels(e.name, e.plugin.title, [_failed(e)], []))
            continue
        plugin_panels.append(
            panels.PluginPanels(e.name, e.plugin.title, own, panels.candidates(own))
        )
    return plugin_panels


class _Person(Model):
    """What the footer reads of a plugin's `person`s: who's me, by handle."""

    handle: str
    me: bool = False


def snoozed(config: Config, db: Database, now: datetime) -> dict[act_now.FeedbackKey, Feedback]:
    """The snoozes in force, by their item's key, as the page sees them: those whose time
    isn't up and whose item hasn't changed since (`act_now.set_aside`). One whose item is in
    no panel and not nominated isn't. One whose item is on ice still is, though the page hides
    the item: the snooze holds once it's thawed. Cheap, as `build_dashboard`."""
    plugins = enabled(config, db, now)
    graph, snapshots = stored_graph(db, plugins)
    current = panels.fingerprints(_plugin_panels(plugins, graph, snapshots))
    return {
        key: f
        for key, f in FeedbackRepository(db).all().items()
        if key in current and act_now.set_aside(current[key], f, now)
    }


def build_dashboard(
    config: Config,
    db: Database,
    now: datetime | None = None,
    surface: Surface = "dashboard",
    selected: Sequence[str] = (),
) -> panels.Dashboard:
    """The dashboard from whatever is stored; cheap, no network. `surface` is where the
    plugins' panels are shown (the dashboard or focus mode), for what other plugins add to
    their items, and `selected` the tags the tag view selects (see `panels.build`)."""
    now = now or datetime.now(UTC)
    plugins = enabled(config, db, now)
    graph, snapshots = stored_graph(db, plugins)
    plugin_panels = _plugin_panels(plugins, graph, snapshots)
    identities = [
        Identity(tool=e.plugin.title, login=", ".join(me))
        for e in plugins
        if (me := [p.handle for p in graph.view(f"{e.name}.{PERSON}", _Person) if p.me])
    ]
    sources = {
        source: SourceStatus(fetched_at=row.fetched_at, error=row.error)
        for source, row in SnapshotRepository(db).of([e.name for e in plugins]).items()
    }

    prepared = [
        (
            e,
            call(
                e.name,
                "item_actions (once a page)",
                lambda e=e: e.plugin.item_actions(Request(e.ctx, graph, snapshots[e.name])),
                adds_nothing,
            ),
        )
        for e in plugins
    ]

    contacts = people.Contacts(ContactRepository(db).all(), graph, config.people)
    tagging = tags.Tags(graph)

    def item_actions(item: Item, owner: str, surface: Surface) -> ItemAdditions:
        found = _merged(contacts.additions(item), tagging.additions(item))
        for e, add in prepared:
            req = ItemRequest(item, owner, surface)
            added = call(
                e.name, "item_actions (per item)", lambda add=add, req=req: add(req), found
            )
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
        tagging.of,
        selected,
    )
    return built.model_copy(
        update={
            "problems": list(registry.DISCOVERY_ERRORS),
            "plugin_problems": ProblemRepository(db).of([e.name for e in plugins]),
        }
    )


def _failed(e: Enabled) -> Panel:
    """What stands in for a plugin's panels when building them failed."""
    return Panel(
        key="failed",
        title=f"{e.plugin.title} (failed)",
        description="Building this plugin's panels failed; the log says why.",
        items=[],
    )


def page_of(path: str) -> tuple[str, str]:
    """The plugin and the page key a path names: `/slack/ping` is slack's `ping`, and
    `/<plugin>` its index, `""`."""
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
        return handler(InputRequest(e.ctx, graph, page=Page(query, path)))
    except Exception as err:
        log.exception("%s: page %s failed", e.name, path)
        raise PageFailed(path) from err


def run_action(config: Config, db: Database, name: str, fields: dict[str, str]) -> str:
    """Run a plugin's action, `<plugin>/<action>`, for its notice; raises `ActionError` when
    it can't."""
    return perform_action(config, db, name, fields).notice


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


def _tag(config: Config, db: Database, action: str, fields: dict[str, str]) -> ActionResult:
    """Tag: put the tag typed as `input` on the entity; Untag: take `tag` off it, whatever put
    it there (see `tags.take_off`)."""
    try:
        ref = Ref(kind=fields["kind"], id=fields["id"])
        tag = tags.tag_key(fields["tag" if action == "untag" else "input"])
    except KeyError as err:
        raise ActionError(f"missing field {err}") from err
    except ValueError as err:
        raise ActionError(str(err)) from err
    now = datetime.now(UTC)
    repo = TagRepository(db)
    if action == "tag":
        return ActionResult(f"tagged #{tag}" if repo.add(ref, tag, now) else f"already #{tag}")
    plugins = enabled(config, db, now)
    graph, _ = stored_graph(db, plugins)  # what the entity's plugin gives it (see `take_off`)
    taken = tags.take_off(repo, graph, ref, tag, now)
    if not taken.on:
        return ActionResult(f"#{tag} wasn't on it")
    return ActionResult(f"#{tag} taken off{taken.back(refreshing(plugins))}")


def refreshing(plugins: list[Enabled]) -> dict[str, str]:
    """The titles of the plugins that refresh (collect), by name."""
    return {e.name: e.plugin.title for e in plugins if e.plugin.collects}


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
    until_changed: bool = False,
) -> datetime | None:
    """Snooze the item a Snooze button's `fields` name, and those shown with it, until each
    changes (its fingerprint) or, unless `until_changed`, `duration` passes (by default as
    long as `config` says for its kind of subject): the Snooze button, and `mfd snooze`. When
    the item's snooze ends; None if only once it changes (or once it's `prune_days` old, and
    pruned)."""

    def ends(kind: str) -> datetime | None:
        if until_changed:
            return None
        return now + duration if duration else config.snooze_until(kind, now)

    feedback = FeedbackRepository(db)
    members = _members(fields)
    for kind, id, task, fingerprint in members:
        feedback.say(
            (kind, id, task),
            Feedback(action="snooze", fingerprint=fingerprint, until=ends(kind)),
            now,
        )
    return ends(members[0][0])  # the item's own: `_members` gives it first


def _act_now_action(
    config: act_now.ActNowConfig, db: Database, action: str, fields: dict[str, str]
) -> ActionResult:
    """Act now's own buttons, on any surface: snooze an item (for as long as `config` says, or
    until it changes, by its `choice`), bring it back (unsnooze), or pin or unpin it, by its
    main subject and task, and the items joined to it (`also`) alike; set or clear the
    priority of its main subject; and set or clear who someone is to me."""
    now = datetime.now(UTC)
    if action in ("priority", "contact", "contact_clear"):
        try:
            if action == "priority":
                return _set_priority(db, fields, now)
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
        # What I did elsewhere isn't a choice to keep: it never replaces a pin or a snooze in
        # force. One that's lapsed (its time is up, or its item has changed) it does.
        said = feedback.all()
        for kind, id, task, f in members:
            before = said.get((kind, id, task))
            if (
                before is None
                or before.action == "opened"
                or (before.action == "snooze" and not act_now.set_aside(f, before, now))
            ):
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
        length = fields.get("choice") or ""
        if length not in act_now.SNOOZE_LENGTHS.values():
            raise ActionError(f"unknown snooze length {length!r}")
        end = snooze_item(config, db, fields, now, until_changed=length == act_now.UNTIL_CHANGED)
        if end is None:
            return ActionResult("snoozed until it changes")
        until = localtime.day(end)
        tomorrow = localtime.day(now) + timedelta(days=1)
        return ActionResult(
            "snoozed until " + ("tomorrow" if until == tomorrow else until.isoformat())
        )
    if action != "pin":
        raise ActionError(f"unknown action {act_now.ACTIONS}/{action}")
    for kind, id, task, f in members:
        feedback.say((kind, id, task), Feedback(action="pin", fingerprint=f), now)
    return ActionResult("pinned")


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
    if plugin_name == act_now.ACTIONS and action_name in ("tag", "untag"):
        return _tag(config, db, action_name, fields)
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
        result = handler(InputRequest(e.ctx, graph, form=Form(fields, surface, back)))
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
