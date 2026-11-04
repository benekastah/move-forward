"""The plugin protocol: what core asks of each external tool's plugin.

A plugin owns one config section, one snapshot (what it fetches on each refresh), its own
tables, and the panels, actions, pages and commands for its tool. Plugins never see each
other's config, snapshots or tables; they learn about each other's data only through the
entity graph (`graph`). Every method has a do-nothing default, so a plugin implements only
what it uses.
"""

import argparse
import logging
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from . import localtime, private, shell
from .asking import Asker, InitSetup
from .db import Database
from .graph import Entity, Graph
from .notifications import Event
from .page import Identity, Item, ItemAdditions, Panel, Surface
from .repositories import KvRepository, SnapshotRepository

log = logging.getLogger(__name__)


class SourceError(Exception):
    """A tool turning us away in a way I have to fix, e.g. a rejected API token. Unlike other
    failures of a plugin's `fetch`, `Graph.fetch` passes it on, so a command can say why
    instead of reporting what it asked about as unknown."""


@dataclass(frozen=True)
class Context[C: BaseModel]:
    """What a plugin gets to work with: its own config section and storage, and the
    settings every plugin shares."""

    config: C
    """The plugin's `[name]` section."""
    email: str
    """My work email."""
    teams: list[str]
    """The names of the teams I'm on or review for; the plugin maps them to its tool's own
    names in its section's `teams` table."""
    prune_days: int
    """Work finished this long ago is cleared by `prune`."""
    now: datetime
    """Now, as an aware time (UTC); show it, or take a day from it, in local time."""
    db: Database
    """For the plugin's own repository over its own tables, which its migrations made."""
    kv: KvRepository
    """Key/value state in the plugin's namespace."""
    dashboard_url: str = ""
    """Where the dashboard is served, e.g. `http://localhost:8765`, for a tool's sign-in to
    come back to. It's `localhost`, which is what such sign-ins take for an app on this
    machine; the dashboard sends it on to 127.0.0.1, where its sign-in cookie is."""
    name: str = ""
    """The plugin's name, as `Plugin.name`."""
    data_root: Path | None = None
    """Where plugins' files go (see `data_dir`): beside the database."""

    @property
    def log(self) -> logging.Logger:
        """The plugin's logger, `move_forward.plugins.<name>`, so its lines say whose they are."""
        return logging.getLogger(f"move_forward.plugins.{self.name}")

    @property
    def data_dir(self) -> Path:
        """A directory of the plugin's own, for files it keeps (a cache, a clone): made on
        first use, private to me like the database beside it."""
        if self.data_root is None or not self.name:
            raise RuntimeError("this context has no data directory")
        path = self.data_root / "plugins" / self.name
        if not path.is_dir():
            path.mkdir(parents=True)
            private.make_private(path)
        return path

    @property
    def today(self) -> date:
        """Today where I am: the local date, for what's recorded as done or changed today."""
        return localtime.day(self.now)


@dataclass(frozen=True)
class EntitiesRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    snapshot: S | None
    """What the plugin last stored; None before its first refresh."""


@dataclass(frozen=True)
class IdentityRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    snapshot: S | None


@dataclass(frozen=True)
class FetchRequest[C: BaseModel]:
    ctx: Context[C]
    kind: str
    ids: list[str]


@dataclass(frozen=True)
class UrlRequest[C: BaseModel]:
    ctx: Context[C]
    kind: str
    id: str


@dataclass(frozen=True)
class LinkingRequest[C: BaseModel]:
    ctx: Context[C]


@dataclass(frozen=True)
class PanelsRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    snapshot: S | None
    graph: Graph


@dataclass(frozen=True)
class ActNowRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    snapshot: S | None
    graph: Graph
    panels: list[Panel]
    """The plugin's own panels, as `panels` built them."""


@dataclass(frozen=True)
class ItemActionsRequest[C: BaseModel]:
    ctx: Context[C]
    item: Item
    graph: Graph
    owner: str
    """The plugin whose item it is."""
    surface: Surface


@dataclass(frozen=True)
class PrepareItemActionsRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph


@dataclass(frozen=True)
class KeptItemRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    key: str
    """What the action put after `<plugin>:` in its `KEPT` parameter."""


PageHandler = Callable[[Context[Any], Graph, dict[str, list[str]]], str | None]
"""Renders a page from the query parameters; None means not found."""
CliHandler = Callable[[Context[Any], Graph, argparse.Namespace], int]
"""Runs `mfd <plugin> ...` with the parsed arguments; returns the exit status."""
KEPT = "kept"
"""The redirect parameter naming an item to keep on the focus page: `<plugin>:<key>`."""


@dataclass(frozen=True)
class ActionResult:
    """What an action did: the notice for the page, and parameters for the page it redirects
    back to."""

    notice: str
    params: dict[str, str] = field(default_factory=dict[str, str])
    """e.g. `{KEPT: "notes:12"}`, to keep a todo just ticked off on the focus page; see
    `Plugin.kept_item`."""


ActionHandler = Callable[[Context[Any], Graph, dict[str, str]], str | ActionResult]
"""Runs a button's action with its form fields; returns the notice for the page (or a result
with redirect parameters too) and raises `ActionError` with the reason when it can't."""


class ActionError(Exception):
    """Something the user needs to know; shown on the page, not a crash."""


class Plugin[C: BaseModel, S: BaseModel]:
    """Base of every plugin. Subclasses set the class attributes and override what they use.

    C is the config section's model; S the snapshot's (`BaseModel` when there is none).
    """

    name: ClassVar[str]
    """e.g. "github": the config section, snapshot source, table prefix and URL segment."""
    title: ClassVar[str]
    """e.g. "GitHub": the heading of the plugin's group on the page."""
    config_model: ClassVar[type[BaseModel]]
    """The model of the `[name]` section: C."""
    kinds: ClassVar[dict[str, type[BaseModel]]] = {}
    """The entity kinds the plugin publishes, each with the model of its attributes. The graph
    checks the plugin's entities of those kinds against them as it's built."""
    linking: ClassVar[dict[str, str | None]] = {}
    """Of its kinds, the ones that link what they're about (see `Graph.related`): each with the
    pattern of its ids, so the graph finds them in other entities' `mentions` (a ticket's
    key), or None when its entities point at what they link themselves (a work entry).
    `linking_patterns` can narrow the patterns from what the plugin has seen."""
    hides: ClassVar[list[str]] = []
    """Of its kinds, the ones whose entities hide what they point at (see `Graph.hidden`):
    core leaves items about hidden things out of every panel but the hiding plugin's own
    items about the hiding entity, e.g. notes' On ice."""
    requires: ClassVar[list[str]] = []
    """Plugins whose entities this one is built on, e.g. `["git"]`; the config must enable
    them too."""

    migrations: ClassVar[list[str]] = []
    """SQL scripts creating and changing the plugin's tables, applied in order. Append to it;
    never edit one that has shipped. Tables are prefixed with `name`."""
    snapshot_model: ClassVar[type[BaseModel] | None] = None
    """S, or None when the plugin fetches nothing and `collect` is never called."""
    collects_after_graph: ClassVar[bool] = False
    """Whether the snapshot comes from `collect_after` instead of `collect`."""
    collect_timeout_seconds: ClassVar[int] = 300
    """How long `collect` (or `collect_after`) may take. Past it, the refresh records the
    plugin as failed and goes on; the plugin is left out of refreshes until its collect
    returns (a thread can't be stopped)."""

    # Refresh

    def collect(self, ctx: Context[C]) -> S:
        """Fetch the plugin's snapshot. Runs in parallel with other plugins' and may raise;
        the error is shown and the last good snapshot kept."""
        raise NotImplementedError(f"{self.name} has a snapshot model but no collect")

    def store(self, ctx: Context[C], snapshot: S) -> None:
        """Keep what `collect` fetched in the plugin's tables, replacing what the last good
        collect kept, in one transaction."""
        raise NotImplementedError(f"{self.name} has a snapshot model but no store")

    def load(self, ctx: Context[C]) -> S | None:
        """What the last good collect kept, for `entities` and `panels`; None when there's
        nothing (no snapshot model, say)."""
        return None

    notification_kinds: ClassVar[dict[str, str]] = {}
    """The events the plugin's `events` makes, by kind, each with what it says, for the
    settings page and `mfd init` to offer: `pr_mergeable` is stored and subscribed to as
    `github.pr_mergeable`."""

    def events(self, ctx: Context[C], before: S, after: S) -> list[Event]:
        """What happened between the last good snapshot (`before`) and the one just collected
        (`after`), as events of its `notification_kinds`; only asked when I've subscribed to
        one of them, and never for the first snapshot, which has nothing to compare with.
        Say it only when the state changed (not mergeable to mergeable), and never about
        something new in `after` that `before` hasn't any state for. A failure is logged and
        the refresh goes on."""
        return []

    def graph_events(self, ctx: Context[C], graph: Graph) -> list[Event]:
        """Events from what other plugins' entities say, e.g. a ticket's PRs all merged,
        asked after every refresh of anything, when I've subscribed to one of its kinds.
        Nothing compares snapshots for it: keep what was said in `ctx.kv` and say a thing
        once, and keep it without saying anything the first time asked."""
        return []

    def collect_after(self, ctx: Context[C], graph: Graph) -> S:
        """Fetch the snapshot when what to fetch comes from other plugins' entities, e.g. the
        repos git found. Runs once their snapshots are stored, in parallel with other
        `collect_after`s, and fails like `collect`."""
        raise NotImplementedError(f"{self.name} collects after the graph but has no collect_after")

    def after_collect(self, ctx: Context[C], graph: Graph) -> None:
        """Work that needs other plugins' entities, once every snapshot is stored, e.g.
        caching lookups in the plugin's tables."""

    change_check_seconds: ClassVar[int | None] = None
    """How often `mfd serve` asks `changed`; None for a plugin without such a check."""
    watches: ClassVar[list[str]] = []
    """Plugins whose data this one's `changed` reads from the graph, e.g. `["git"]`: after
    each refresh of one of them, `mfd serve` asks `changed` at once, with the new graph,
    rather than waiting for `change_check_seconds`."""

    def changed(self, ctx: Context[C], graph: Graph) -> bool:
        """Whether what the plugin collects has changed since this was last asked, by a check
        far cheaper than `collect` (files' times, a request answered "not modified", other
        plugins' entities in `graph`, built from what's stored), so the plugin refreshes soon
        after a change rather than at its next `refresh_seconds`. Keep what it compares
        against in `ctx.kv`; with nothing kept yet, keep it and say False. Core asks once more after each refresh of the plugin and ignores the answer,
        so what the refresh did itself (`git status` rewriting the index) doesn't count. May
        raise; the error is logged and the next check tries again."""
        return False

    # Reading

    def linking_patterns(self, req: LinkingRequest[C]) -> dict[str, str | None]:
        """Its `linking` kinds with the patterns of their ids as of now, e.g. only the project
        keys jira has seen; None where no id can be found in text. Read from its tables, so
        cheap: core asks every time it builds the graph."""
        return dict(self.linking)

    def identity(self, req: IdentityRequest[C, S]) -> Identity | None:
        """Who I am in the plugin's tool (a login), for the page's footer, which lists each
        plugin's; None when it doesn't say."""
        return None

    def entities(self, req: EntitiesRequest[C, S]) -> list[Entity]:
        """What the plugin publishes to the graph, from its snapshot and tables."""
        return []

    def fetch(self, req: FetchRequest[C]) -> list[Entity]:
        """Entities of a kind the plugin publishes, by id, fetched from its tool now, even ones
        its snapshot doesn't hold; `Graph.fetch` asks. [] for a kind it doesn't know."""
        return []

    def url_for(self, req: UrlRequest[C]) -> str | None:
        """A URL for an entity of a kind the plugin knows, even one not in the graph, e.g. a
        ticket's from its key."""
        return None

    # Page

    def panels(self, req: PanelsRequest[C, S]) -> list[Panel]:
        return []

    def act_now(self, req: ActNowRequest[C, S]) -> list[Item]:
        """The plugin's candidates for core's Act now, each with `signals`, from its
        `panels`. By default the items of the panels marked `act_now`, each taking its
        panel's signals if it has none of its own; a plugin that knows better nominates
        precisely."""
        return [
            item if item.signals else item.model_copy(update={"signals": panel.signals})
            for panel in req.panels
            if panel.act_now
            for item in panel.items
        ]

    def item_actions(self, req: ItemActionsRequest[C]) -> ItemAdditions:
        """What the plugin adds to another plugin's item: buttons (e.g. "add todo" on a PR),
        badges, details and links."""
        return ItemAdditions()

    def prepare_item_actions(
        self, req: PrepareItemActionsRequest[C]
    ) -> Callable[[ItemActionsRequest[C]], ItemAdditions]:
        """`item_actions` for every item of one page: a plugin whose additions need its
        tables or a pass over the graph does that here, once, and returns the per-item
        function."""
        return self.item_actions

    def kept_item(self, req: KeptItemRequest[C]) -> tuple[str, Item] | None:
        """An item one of this plugin's actions asked to keep on the focus page after changing
        it (its result's `KEPT` parameter, `<plugin>:<key>`), e.g. a todo just ticked off,
        shown with its box ticked so the tick can be undone; with the key of the plugin's
        panel it belongs to."""
        return None

    def pages(self) -> dict[str, PageHandler]:
        """Extra pages by path, e.g. "/ping"."""
        return {}

    sign_in_pages: ClassVar[frozenset[str]] = frozenset()
    """Of its pages, those a tool's sign-in (OAuth) sends the browser back to, e.g.
    "/slack/callback". They're served without the dashboard's sign-in cookie, which browsers
    don't send on a redirect from another site, so the page must check its own proof that
    the request is the one it started (OAuth's single-use `state`) before doing anything."""

    # Writing

    def actions(self) -> dict[str, ActionHandler]:
        """Button handlers by name, posted to `/actions/<plugin>/<name>`."""
        return {}

    cli_help: ClassVar[str | None] = None
    """The help line of `mfd <name>`; None means the plugin has no command."""

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        """Add the arguments of `mfd <name>` to `parser` and return what runs it. Only called
        when `cli_help` is set."""
        raise NotImplementedError(f"{self.name} has cli_help but no cli")

    # Set-up and upkeep

    always_on: ClassVar[bool] = False
    """On without a section (notes), so `mfd init` doesn't offer it."""
    init_template: ClassVar[str] = ""
    """The section's optional settings with their defaults, commented out, as `mfd init`
    writes them under the section."""
    init_example: ClassVar[str] = ""
    """The section's required settings, commented out, for a section `mfd init` writes
    commented out because the plugin wasn't chosen."""

    init_finds_teams: ClassVar[bool] = False
    """Its `init_questions` set `setup.teams` (github's), so core needn't ask for them."""

    def init_suggested(self) -> bool:
        """Whether `mfd init` offers to set the plugin up by default."""
        return True

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        """Ask for the `[name]` section's settings in `mfd init`; returns its values (a
        table's as a dict, e.g. `{"teams": {...}}` for `[name.teams]`). `setup` has what
        init knows so far; a plugin can add to its `teams`."""
        return {}

    def prune(self, ctx: Context[C], graph: Graph) -> list[str]:
        """Daily: drop the plugin's data about work finished over `prune_days` ago. Returns
        what was done, one line each."""
        return []


def form_field(fields: Mapping[str, str], name: str) -> str:
    """An action's request field; `ActionError` when the request lacks it."""
    try:
        return fields[name]
    except KeyError:
        raise ActionError(f"missing field {name!r}") from None


def collect_into[T: BaseModel](
    db: Database,
    source: str,
    now: datetime,
    collector: Callable[[], T],
    store: Callable[[T], None],
) -> T | None:
    """Run a collector and keep its result with `store` (into a plugin's tables), recording
    when as the `source`'s status; a failure is recorded as its error instead, and the last
    good result stays. Returns the result, or None if it failed."""
    snapshots = SnapshotRepository(db)
    try:
        result = collector()
        store(result)  # one transaction: on failure, the last good result is still there
    except Exception as e:  # noqa: BLE001 - one failing source must not stop the others
        if shell.stopping():
            return None  # ctrl-c interrupted the commands; nothing to record
        log.warning("%s failed: %s", source, e)
        log.debug("%s", traceback.format_exc())
        snapshots.save_error(source, now, f"{type(e).__name__}: {e}")
        return None
    snapshots.save_fetched(source, now)
    return result
