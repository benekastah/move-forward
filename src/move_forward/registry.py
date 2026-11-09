"""The built-in plugins, and the enabled ones with their contexts."""

import importlib
import inspect
import logging
import pkgutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from . import act_now, notifications, tags
from . import plugins as builtin_plugins
from .db import CORE, OWNER_RE, Database, core_objects
from .domain import KindModel
from .graph import Entity, Graph, KindSpec, Ref
from .keychain import TokenSettings
from .kinds import CORE_FAMILIES, FAMILIES, TAG, declare, declaring, fits, named_for
from .plugin import (
    API_VERSION,
    Context,
    EntitiesRequest,
    FetchRequest,
    HandleRequest,
    LinkingRequest,
    LoadRequest,
    Plugin,
    RelateRequest,
    SourceError,
    UrlRequest,
)
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


def builtin(package: str) -> Plugin[Any, Any]:
    """The plugin a built-in package defines: the one `Plugin` subclass in its `plugin` module,
    named as the package is. Raises TypeError for a package with no `plugin` module, one
    defining no plugin or several, or a plugin named otherwise."""
    try:
        module = importlib.import_module(f"{package}.plugin")
    except ModuleNotFoundError as e:
        if e.name != f"{package}.plugin":
            raise  # something the plugin imports
        raise TypeError(f"{package} has no plugin.py: every package in plugins/ is one") from e
    found: list[type[Plugin[Any, Any]]] = [
        cast(type[Plugin[Any, Any]], cls)
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, Plugin) and cls.__module__ == module.__name__
    ]
    if len(found) != 1:
        raise TypeError(f"{module.__name__} defines {len(found)} Plugin subclasses, not one")
    plugin, expected = found[0](), package.rpartition(".")[2]
    if plugin.name != expected:
        raise TypeError(f"{module.__name__}'s plugin is named {plugin.name!r}, not {expected!r}")
    return plugin


BUILTINS: list[Plugin[Any, Any]] = [
    builtin(f"{builtin_plugins.__name__}.{found.name}")
    for found in pkgutil.iter_modules(builtin_plugins.__path__)
    if found.ispkg
]
"""The plugins that come with the dashboard: each package in `move_forward/plugins/`, by the
`Plugin` subclass its `plugin.py` defines, named as the package is, so adding one needs no
change here. Found in the
package rather than by entry points, so a checkout run without installing it (or a venv whose
metadata is stale) still has them."""
ENTRY_POINTS = "move_forward.plugins"
"""The entry point group other packages' plugins are found in: each names a `Plugin`
subclass, e.g. `linear = "move_forward_linear:LinearPlugin"`."""
RESERVED_NAMES = frozenset(
    {
        # Core's own owners, keys and namespaces.
        CORE,
        act_now.ACTIONS,
        "act",
        "dashboard",
        "plugins",
        "static",
        # The config's top-level keys.
        "act_now",
        "db_backups",
        "db_path",
        "email",
        "https",  # gone, and refused as such: a plugin of that name would be too
        "notifications",
        "people",
        "port",
        "prune_days",
        "refresh_on_load_seconds",
        "sections",
        "teams",
        # Core's commands, `mfd <command>`.
        "check",
        "contact",
        "contacts",
        "dump",
        "init",
        "logout",
        "open",
        "priority",
        "refresh",
        "restart",
        "serve",
        "snooze",
        "snoozes",
        "tag",
        "token",
        "unsnooze",
        # Core's paths' first segments (`server.RESERVED_PATHS`).
        "actions",
        "favicon-refresh.svg",
        "favicon-refresh-1.svg",
        "favicon-refresh-2.svg",
        "favicon.svg",
        "focus",
        "layout",
        "login",
        "logo.svg",
        "settings",
        "style.css",
    }
)
"""What no plugin may be named: a plugin's name is its config section, its `mfd` command,
the first segment of its pages' and actions' paths, its kv namespace and its tables'
prefix, so it can't be any of core's. `tests/test_registry.py` checks the list against the
config, the commands and the server."""


_CORE_FAMILY_NAMES = {family.name for family in CORE_FAMILIES}


def check_plugins(plugins: list[Plugin[Any, Any]]) -> None:
    """Raise unless every plugin is built against this dashboard's `API_VERSION`, and its
    name is its own and fit to be one: lower-case, not two plugins', none of core's
    (`RESERVED_NAMES`), and no prefix of core's tables or indexes (`team` of `team_channels`).
    A plugin's kinds are named for it (`git.branch`), or for a family, core's or one it
    declares (`Plugin.families`), whose model they must extend."""
    names = [p.name for p in plugins]
    if duplicates := sorted({name for name in names if names.count(name) > 1}):
        raise RuntimeError(f"two plugins are named {', '.join(duplicates)}")
    for name in names:
        if not OWNER_RE.match(name):
            raise RuntimeError(f"plugin name {name!r}: use lower-case letters, digits and _")
        if name in RESERVED_NAMES:
            raise RuntimeError(f"plugin name {name!r} is core's")
        if owned := sorted(t for t in core_objects() if t.startswith(f"{name}_")):
            # Its tables are named `<name>_...`: these would be its too.
            raise RuntimeError(f"plugin name {name!r} would own core's {', '.join(owned)}")
    for plugin in plugins:
        if plugin.api_version != API_VERSION:
            raise RuntimeError(
                f"{plugin.name} is built against API version {plugin.api_version}; this"
                f" dashboard's is {API_VERSION}"
            )
    for plugin in plugins:
        for kind, spec in plugin.kinds.items():
            family = FAMILIES.get(kind)
            usable = _CORE_FAMILY_NAMES | {f.name for f in plugin.families}
            if family is None and not named_for(plugin.name, kind):
                raise RuntimeError(
                    f"{plugin.name}'s kind {kind!r} must be named {plugin.name}.<name>,"
                    f" or for a family: {', '.join(sorted(usable))}"
                )
            # The type says so, but a plugin needn't be type-checked.
            if not issubclass(cast(type[BaseModel], spec.attrs), KindModel):
                raise TypeError(
                    f"{plugin.name}'s {kind} attributes must be a KindModel, not"
                    f" {spec.attrs.__name__}"
                )
            if family is not None and kind not in usable:
                raise RuntimeError(
                    f"{plugin.name}'s kind {kind} is a family it doesn't declare in its families"
                )
            if family is not None and not issubclass(spec.attrs, family.model):
                raise RuntimeError(
                    f"{plugin.name}'s {kind} must extend {family.model.__name__}"
                    f" (the {family.label} family), not {spec.attrs.__name__}"
                )


def discover() -> tuple[list[Plugin[Any, Any]], list[str]]:
    """Every plugin, by name: the built-ins and the other packages' (`ENTRY_POINTS`), with
    what went wrong loading one. A plugin that won't import, isn't a `Plugin` or fails
    `check_plugins` (a built-in's name, a reserved one) is left out, and said why; a
    built-in failing it is a bug, and raises. Each plugin's families (`Plugin.families`) are
    declared before it's checked, so its kinds can be of them; a plugin left out declares
    none."""
    plugins = list(BUILTINS)
    for builtin in plugins:
        declare(builtin.name, builtin.families)
    check_plugins(plugins)
    errors: list[str] = []
    for point in entry_points(group=ENTRY_POINTS):
        try:
            loaded: object = point.load()
            plugin: object = loaded() if isinstance(loaded, type) else loaded
            if not isinstance(plugin, Plugin):
                raise TypeError(f"{point.value} isn't a Plugin")
            installed = cast(Plugin[Any, Any], plugin)
            with declaring(installed.name, installed.families):
                check_plugins([*plugins, installed])
        except Exception as e:  # noqa: BLE001 (any failure leaves just this plugin out)
            errors.append(f"plugin {point.name} ({point.value}) is left out: {e}")
            continue
        plugins.append(installed)
    return sorted(plugins, key=lambda p: p.name), errors


PLUGINS, DISCOVERY_ERRORS = discover()
"""Every plugin, built-in or installed, in name order; and why any installed one is left
out (`mfd check` says). Each one's name is its section in the config (`Config.section`)."""


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
        with self.ctx.db.reading():  # all of one refresh's, never half of the next's
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
    return [p.name for p in PLUGINS if config.section(p.name) is not None]


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


def tokens(config: "Config") -> dict[str, TokenSettings]:
    """Where each enabled plugin keeps each of its tokens (`Plugin.tokens`), by the name
    `mfd token set` takes: the plugin's, or `<plugin>.<account>` for one of several."""
    found: dict[str, TokenSettings] = {}
    for plugin in PLUGINS:
        section = config.section(plugin.name)
        if section is not None:
            for name, settings in plugin.tokens(section).items():
                found[f"{plugin.name}.{name}" if name else plugin.name] = settings
    return found


def shared_token_services(config: "Config") -> list[str]:
    """The keychain services more than one token is kept under (as `tokens` says), each with
    whose, e.g. "jira ('Shared') and slack ('shared') keep their tokens in one place": storing
    one token would replace the other's. Case doesn't count, on every system, as Windows'
    Credential Manager ignores it: one config means the same everywhere. Services a plugin
    derives from its own (Slack's `-refresh`) aren't counted."""
    by_service: dict[str, list[tuple[str, str]]] = {}
    for name, settings in tokens(config).items():
        service = settings.keychain_service
        by_service.setdefault(service.casefold(), []).append((name, service))
    return [
        f"{' and '.join(f'{name} ({service!r})' for name, service in who)} keep their tokens in"
        " one place (case doesn't count): give each its own keychain_service"
        for who in by_service.values()
        if len(who) > 1
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
    owners = [p.name for p in PLUGINS]
    for plugin in PLUGINS:
        if config.section(plugin.name) is not None:
            db.migrate(plugin.name, plugin.migrations, owners)


def enabled(config: "Config", db: Database, now: datetime) -> list[Enabled]:
    """The plugins with a config section, with their contexts. Their tables are there once
    `migrate` has run."""
    result: list[Enabled] = []
    for plugin in PLUGINS:
        section = config.section(plugin.name)
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
            dashboard_url=f"http://localhost:{config.port}",
            name=plugin.name,
        )
        result.append(Enabled(plugin, ctx))
    return result


def _unfit_fetched(entity: Entity, spec: KindSpec) -> str | None:
    """Why a fetched entity doesn't fit its kind, or None: it must say its `lifecycle`, and
    its `attrs` fit its family's fetched model, or else are fields of `spec.attrs`, each of
    its type."""
    if "lifecycle" not in entity.model_fields_set:
        return "no lifecycle"
    family = FAMILIES.get(entity.ref.kind)
    model = family.fetched if family else None
    try:
        if model is not None:
            model.model_validate(entity.attrs)
            return None
        fields = spec.attrs.model_fields
        for name, value in entity.attrs.items():
            if name not in fields:
                return f"no attribute {name!r}"
            TypeAdapter[object](fields[name].annotation or object).validate_python(value)
    except ValidationError as error:
        return str(error)
    return None


def build_graph(
    plugins: list[Enabled],
    snapshots: dict[str, BaseModel | None],
    tagged: Mapping[Ref, list[str]] | None = None,
) -> Graph:
    """The graph of every plugin's entities. A plugin publishes only the kinds it declares;
    each entity must fit its kind's model, and be the only one of its ref (the first
    stands when two plugins publish one entity of a family), with an id its family takes
    (`kinds.Family.id_rule`). One that isn't is a plugin bug:
    it's logged and left out, and the page goes on without it. So is a plugin whose
    `entities`, `url_for` or `linking_patterns` raises: the graph is built without its
    answer. Core adds the `tag`s on the entities, in their titles or `tagged` by hand, and
    points those whose text names one at it (see `tags`)."""
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
            if ref.kind not in e.plugin.kinds:
                log.error("%s published %s %s, a kind it doesn't declare", e.name, ref.kind, ref.id)
                continue
            if not fits(ref.kind, ref.id):
                log.error(
                    "%s published %s %s, an id its family doesn't take", e.name, ref.kind, ref.id
                )
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
            family = ref.kind if ref.kind in FAMILIES else None
            kind = f"{e.name}.{family}" if family else ref.kind
            entities.append(entity.model_copy(update={"family": family, "kind": kind}))

    def url_for(kind: str, id: str) -> str | None:
        for e in plugins:
            url: str | None = call(
                e.name, "url_for", lambda e=e: e.plugin.url_for(UrlRequest(e.ctx, kind, id)), None
            )
            if url:
                return url
        return None

    def handle_for(kind: str, id: str) -> str | None:
        for e in plugins:
            handle: str | None = call(
                e.name,
                "handle_for",
                lambda e=e: e.plugin.handle_for(HandleRequest(e.ctx, kind, id)),
                None,
            )
            if handle:
                return handle
        return None

    def fetch(kind: str, ids: list[str]) -> list[Entity]:
        """Each plugin's answer; one that fails is logged and left out, so its entities just
        aren't known (the prune keeps what it can't check). So is an entity that doesn't fit
        what its kind's fetched entities carry (see `_unfit_fetched`)."""
        found: list[Entity] = []
        for e in plugins:
            try:
                answer = e.plugin.fetch(FetchRequest(e.ctx, kind, ids))
            except SourceError:
                raise
            except Exception:
                log.exception("%s: fetching %s failed", e.name, kind)
                continue
            for entity in answer:
                spec = e.plugin.kinds.get(entity.ref.kind)
                if spec is None:
                    log.error("%s fetched %s, a kind it doesn't declare", e.name, entity.ref.kind)
                elif problem := _unfit_fetched(entity, spec):
                    log.error("%s fetched a bad %s %s: %s", e.name, kind, entity.ref.id, problem)
                else:
                    found.append(entity)
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
    specs: dict[str, KindSpec] = {TAG: tags.SPEC}
    for e in plugins:
        for kind, spec in e.plugin.kinds.items():
            specs.setdefault(kind, spec)  # a family's first publisher's
    entities = tags.tagged(entities, specs, tagged)
    hiding = [kind for kind, spec in specs.items() if spec.hides]
    graph = Graph(entities, url_for, fetch, linking, hiding, specs, handle_for)
    related = _relations(plugins, graph)
    if not related:
        return graph
    entities = [
        e.model_copy(update={"refs": list(dict.fromkeys([*e.refs, *related[e.ref]]))})
        if e.ref in related
        else e
        for e in entities
    ]
    return Graph(entities, url_for, fetch, linking, hiding, specs, handle_for)


def _relations(plugins: list[Enabled], graph: Graph) -> dict[Ref, list[Ref]]:
    """What each plugin relates (`Plugin.relate`), by the entity of its own each starts at.
    A relation from an entity that isn't the plugin's, or isn't in the graph, is a plugin
    bug: it's logged and left out."""
    found: dict[Ref, list[Ref]] = {}
    for e in plugins:
        pairs: list[tuple[Ref, Ref]] = call(
            e.name, "relate", lambda e=e: e.plugin.relate(RelateRequest(e.ctx, graph)), []
        )
        for mine, theirs in pairs:
            if mine.kind not in e.plugin.kinds or graph.get(mine.kind, mine.id) is None:
                log.error("%s related %s %s, which it doesn't publish", e.name, mine.kind, mine.id)
                continue
            found.setdefault(mine, []).append(theirs)
    return found
