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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from . import keychain, localtime, shell
from .act_now import DUE_HORIZON
from .asking import Asker, InitSetup
from .db import Database, Migration
from .graph import Entity, Graph, KindSpec, Ref
from .keychain import TokenSettings
from .kinds import Family
from .notifications import Event
from .page import Identity, Item, ItemAdditions, Panel, Surface
from .repositories import KvRepository, ProblemRepository, SnapshotRepository

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
    name: str = ""
    """The plugin's name, as `Plugin.name`."""
    tokens: Mapping[str, TokenSettings] | None = None
    """Where each of the plugin's tokens is, as `Plugin.tokens` says, for `token`; None for
    its default: the section's own, when it says where a token is."""

    @property
    def log(self) -> logging.Logger:
        """The plugin's logger, `move_forward.plugins.<name>`, so its lines say whose they are."""
        return logging.getLogger(f"move_forward.plugins.{self.name}")

    @property
    def today(self) -> date:
        """Today where I am: the local date, for what's recorded as done or changed today."""
        return localtime.day(self.now)

    def kept(self, key: str) -> dict[str, str]:
        """An action result's parameters keeping the plugin's item `key` on the focus page
        (`ActionResult.params`); `Plugin.kept_item` gets `key` back."""
        return {KEPT: f"{self.name}:{key}"}

    def part(self, name: str) -> str:
        """The source a part of the plugin's refresh is recorded as, e.g. github's `derived`
        as `github.derived` (see `collect_into`)."""
        return f"{self.name}.{name}"

    def problem(self, key: str, text: str | None) -> None:
        """Report something wrong that isn't the plugin's refresh failing (a lookup its token
        isn't allowed, two clones of one repository) as its problem `key`, or with None that
        it's gone. Core shows it with the plugin's state on the page (its group's summary, or
        the footer) and keeps it across restarts until it's cleared, or until it's gone
        unreported for `prune_days` (a key the plugin no longer uses); it's logged only when it
        changes, so a plugin can report it on every refresh."""
        before = ProblemRepository(self.db).set(self.name, key, text, self.now)
        if text == before:
            return
        if text is not None:
            self.log.warning("%s", text)
        else:
            self.log.info("no longer: %s", before)

    def token(self, account: str | None = None, *, secret: str = "token") -> str:
        """The plugin's token, or that of its account `account` (see `Plugin.tokens`; `""` is
        the plugin's own too): from its `token_env`, else from the system's secret store under
        its `keychain_service` for `email`. Raises SourceError, saying how to give one and
        calling it `secret` (mail's is a "password"), when neither has it, and LookupError for
        an account the plugin keeps no token for."""
        kept = self.tokens if self.tokens is not None else _section_token(self.config)
        name = account or ""
        settings = kept.get(name)
        if settings is None:
            raise LookupError(f"{self.name} keeps no token for {name!r}")
        if value := keychain.token(settings, self.email):
            return value
        stored = f"{self.name}.{name}" if name else self.name
        raise SourceError(
            f"no {secret}: run `mfd token set {stored}`, or set ${settings.token_env}"
        )


def _section_token(config: BaseModel) -> dict[str, TokenSettings]:
    """A plugin's tokens by default (`Plugin.tokens`): its section's own, as `""`, when the
    section has `token_env` and `keychain_service`; else none."""
    return {"": config} if isinstance(config, TokenSettings) else {}


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
class HandleRequest[C: BaseModel]:
    ctx: Context[C]
    kind: str
    id: str


@dataclass(frozen=True)
class LinkingRequest[C: BaseModel]:
    ctx: Context[C]


@dataclass(frozen=True)
class SubjectsRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    refs: list[Ref]
    """What to fetch again: entities the plugin publishes, at most `MAX_SUBJECTS`."""
    snapshot: S | None
    """What the plugin last stored, to fold them into; None before its first refresh."""


@dataclass(frozen=True)
class Subjects[S: BaseModel]:
    """What `refresh_subjects` fetched: the plugin's snapshot with them folded in."""

    snapshot: S
    """Kept with `store`, as a refresh's would be."""
    updated: list[Ref] = field(default_factory=list[Ref])
    """The refs fetched; the rest the tool didn't know."""


MAX_SUBJECTS = 50
"""How many entities one refresh of subjects fetches at most."""


@dataclass(frozen=True)
class RelateRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    """Every plugin's entities, before any relations."""


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
    due_horizon: timedelta = DUE_HORIZON
    """How far ahead a due date starts to count, as `[act_now] due_warning_days` says; a plugin
    nominating by its due dates passes it to `due_pressure`."""


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


@dataclass(frozen=True)
class CollectRequest[C: BaseModel]:
    ctx: Context[C]
    full: bool = True
    """Whether to fetch everything: when the plugin's `refresh_seconds` have passed since the
    last full collect that stored, when its change checks saw something move that no subject
    names, and when they named nothing. Otherwise fetching `subjects` is enough, keeping the
    rest as stored."""
    subjects: frozenset[str] = frozenset()
    """What the plugin's change checks said moved (`Changed.subjects`) since a collect given
    it last stored: core keeps it until one does, so a collect that fails, or a store that
    does, loses none of it."""


@dataclass(frozen=True)
class CollectAfterRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    """The graph with what the plugins collecting first just stored."""
    full: bool = True
    """As `CollectRequest.full`."""
    subjects: frozenset[str] = frozenset()
    """As `CollectRequest.subjects`."""


@dataclass(frozen=True)
class StoreRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    snapshot: S


@dataclass(frozen=True)
class LoadRequest[C: BaseModel]:
    ctx: Context[C]


@dataclass(frozen=True)
class EventsRequest[C: BaseModel, S: BaseModel]:
    ctx: Context[C]
    before: S
    """The plugin's snapshot before this refresh: after a start, what was stored when the
    dashboard stopped."""
    after: S
    """Its snapshot now: the same as `before` when this refresh didn't fetch it."""
    previous_graph: Graph
    """The graph as it was before this refresh."""
    graph: Graph
    """The graph now."""


@dataclass(frozen=True)
class AfterCollectRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph


@dataclass(frozen=True)
class ChangedRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    """Every plugin's entities, built from what's stored now."""
    previous_graph: Graph | None = None
    """The graph as it was at the plugin's last change check that didn't raise, to compare
    with `graph` for what other plugins' refreshes moved since (a plugin that `watches`
    them); None at the first check since the dashboard started."""
    fingerprint: str | None = None
    """The fingerprint of the last answer core took (`Changed.fingerprint`), for a plugin
    that tells what moved by comparing its parts; None before the first."""
    checked_at: datetime | None = None
    """When core took that answer, for a tool asked what moved since a time; None before the
    first."""


@dataclass(frozen=True, kw_only=True)
class Changed:
    """`changed`'s answer: what moved in the plugin's tool, which core keeps (see
    `Plugin.changed`). `Changed()` says nothing moved."""

    fingerprint: str | None = None
    """The tool's state now, as text that changes whenever it moves (a digest, a mailbox's
    counts); None for a plugin that has none. Core keeps it and compares the next with it."""
    subjects: frozenset[str] = frozenset()
    """What moved, in the plugin's own terms (repos, PRs), for `collect` to fetch just
    those (`CollectRequest.subjects`)."""
    everything: bool = False
    """Something moved that `subjects` doesn't name, so the next collect is a full one."""
    reason: str = ""
    """What changed, e.g. "notifications about 2 PRs"; it goes into the log line."""


@dataclass(frozen=True)
class PruneRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph


@dataclass(frozen=True)
class PageRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    query: dict[str, list[str]]
    path: str
    """The page's path, e.g. `/slack/ping`."""


@dataclass(frozen=True)
class Response:
    """What a page answers when it's more than HTML to show: a redirect, another status."""

    body: str = ""
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict[str, str])

    @classmethod
    def redirect(cls, url: str) -> "Response":
        """Send the browser to `url` (303 See Other)."""
        return cls(status=303, headers={"Location": url})


def page_path(plugin: str, key: str) -> str:
    """Where a plugin's page is served: `/<plugin>/<key>`, or `/<plugin>` for its index
    (`""`)."""
    return f"/{plugin}/{key}" if key else f"/{plugin}"


@dataclass(frozen=True)
class CliRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    args: argparse.Namespace


@dataclass(frozen=True)
class ImportRequest[C: BaseModel]:
    """`mfd import <dir>`: what a plugin reads from `dir`, the move-forward skill's markdown
    notes (`work-notes.md`, `on-ice.md`, `reviewer-map.md`)."""

    ctx: Context[C]
    dir: Path
    replace: bool
    """`--replace`: overwrite what's there where the plugin would otherwise keep it and skip
    the import."""


PageHandler = Callable[[PageRequest[Any]], str | Response | None]
"""Renders a page, as HTML or a `Response`; None means not found."""
CliHandler = Callable[[CliRequest[Any]], int]
"""Runs `mfd <plugin> ...` with the parsed arguments; returns the exit status."""
API_VERSION = 2
"""The version of `move_forward.api` this dashboard offers. A change that would break a
plugin built against it (a hook's request losing a field, a name leaving `api`) raises it;
see `docs/api-changes.md`."""
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
    touched: list[Ref] = field(default_factory=list[Ref])
    """What the action changed outside its own plugin's tool, e.g. a PR it reacted to: the
    server refreshes the plugin that publishes each, as well as the acting plugin."""


class ActionError(Exception):
    """Something the user needs to know; shown on the page, not a crash."""


@dataclass(frozen=True)
class ActionRequest[C: BaseModel]:
    ctx: Context[C]
    graph: Graph
    fields: dict[str, str]
    """The button's form fields: untrusted, as anything a form sends."""
    surface: Surface = "dashboard"
    """Where the button was: the dashboard or focus mode."""
    back: str | None = None
    """The page the button was on, when it wasn't the dashboard (a focus page)."""

    def field(self, name: str) -> str:
        """A required field; `ActionError` saying "missing field" when the form lacks it."""
        try:
            return self.fields[name]
        except KeyError:
            raise ActionError(f"missing field {name!r}") from None


ActionHandler = Callable[[ActionRequest[Any]], str | ActionResult]
"""Runs a button's action; returns the notice for the page (or a result with redirect
parameters too) and raises `ActionError` with the reason when it can't."""


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
    families: ClassVar[tuple[Family, ...]] = ()
    """Families of kinds the plugin declares (`kinds.Family`): what more than one tool may
    represent, like core's pull request, ticket and person, with the model its kinds extend and
    the rule its ids follow; its model needs a `handle`. Declared by the plugin, or by a
    package several share, it needs no change to core. A plugin publishes kinds only of core's
    families and those it declares, and one declaring a family differently from core or
    another plugin is left out, and said why."""
    kinds: ClassVar[dict[str, KindSpec]] = {}
    """The entity kinds the plugin publishes, each with its spec: the model of its
    attributes, which the graph checks its entities against, and whether it links what it's
    about (see `Graph.related`; `linking_patterns` gives its id pattern), hides what it
    points at (core leaves items about hidden things off the page, but for the hiding
    plugin's own, e.g. notes' On ice), can be fetched, is done once gone, or is where `mfd
    priority` looks."""
    requires: ClassVar[list[str]] = []
    """Plugins whose entities this one is built on, e.g. `["git"]`; the config must enable
    them too."""
    migrations: ClassVar[list[Migration]] = []
    """SQL scripts creating and changing the plugin's tables, applied in order. Append to it;
    never edit one that has shipped. Tables are prefixed with `name`."""
    snapshot_model: ClassVar[type[BaseModel] | None] = None
    """S, or None when the plugin fetches nothing and `collect` is never called (see
    `collects`)."""
    api_version: ClassVar[int | None] = None
    """The `API_VERSION` the plugin was built against: one built against another is left
    out, with the reason in `mfd check` and the page's footer."""
    teams_field: ClassVar[str | None] = None
    """The field of the plugin's section mapping each top-level team to what the tool calls
    it (`teams`, e.g. `[github.teams]`), so the config can check it names only teams it has;
    None for a plugin without one."""
    notification_kinds: ClassVar[dict[str, str]] = {}
    """The events the plugin's `events` makes, by kind, each with what it says, for the
    settings page and `mfd init` to offer: `pr_mergeable` is stored and subscribed to as
    `github.pr_mergeable`."""
    change_check_seconds: ClassVar[int | None] = None
    """How often `mfd serve` asks `changed`; None for a plugin without such a check. It's
    asked as each collect starts too, whether it's set or the plugin `watches` another."""
    watches: ClassVar[list[str]] = []
    """Plugins whose data this one's `changed` reads from the graph, e.g. `["git"]`: after
    each refresh of one of them, `mfd serve` asks `changed` at once, with the new graph,
    rather than waiting for `change_check_seconds`."""
    cli_help: ClassVar[str | None] = None
    """The help line of `mfd <name>`; None means the plugin has no command."""
    always_on: ClassVar[bool] = False
    """On without a section (notes), so `mfd init` doesn't offer it."""
    init_tables: ClassVar[str] = ""
    """Commented-out tables of the section's that its model can't show, as `mfd init` writes
    them after its optional keys: a table keyed by name, with example entries (`[github.teams]`,
    `[mail.<name>]`). A key written here as `[<section>.<key>]` isn't written as a key too.
    `mfd init` writes each other optional key itself, from `config_model`: its default, and its
    docstring's first sentence as its comment (`init_shown` for one it shows otherwise)."""
    init_example: ClassVar[str] = ""
    """The section's required settings with example values, commented out, for a section `mfd
    init` writes commented out because the plugin wasn't chosen. `mfd init --example` writes
    them uncommented for a plugin `init_example_on`."""
    init_example_on: ClassVar[bool] = False
    """`mfd init --example` writes the section on, with `init_example`'s values, rather than
    commented out like a plugin `mfd init` wasn't asked for: for a tool most use. Off by
    default, so a plugin whose `init_example` lacks a required key can't make the example
    a config that won't load."""
    init_finds_teams: ClassVar[bool] = False
    """Its `init_questions` set `setup.teams` (github's), so core needn't ask for them."""
    locked_settings: ClassVar[frozenset[str]] = frozenset()
    """Its settings the settings page shows but won't change, as they say where a credential
    goes (a site, a host) or what runs where. Its token's `token_env` and `keychain_service`
    are locked without naming them; settings its `tokens` reads a token's place from under
    other names, or builds it from, it names here."""
    token_help: ClassVar[str] = ""
    """`mfd init`'s next step for its token(s), in place of the plain "`mfd token set <name>`
    stores its token": where to get one, or another way in (signing in in the browser)."""

    @property
    def collects(self) -> bool:
        """Whether the plugin fetches anything: it has a `snapshot_model`, so a refresh
        calls its `collect` (or `collect_after`) and it has a schedule."""
        return self.snapshot_model is not None

    @property
    def collects_after_graph(self) -> bool:
        """Whether the snapshot comes from `collect_after` instead of `collect`: the plugin
        implements `collect_after`."""
        return any("collect_after" in vars(c) for c in type(self).__mro__ if c is not Plugin)

    def tokens(self, config: C) -> dict[str, TokenSettings]:
        """Every token the plugin keeps, for `mfd token`, by name: `""` for its own, and one per
        account for a plugin reading several (several mailboxes, say), stored as
        `<plugin>.<name>`. By default its section is its own token's place, when that has
        `token_env` and `keychain_service`. Each needs a `keychain_service` of its own: the
        config is refused when two tokens share one. Its context's `token` reads them."""
        return _section_token(config)

    # Refresh

    def collect(self, req: CollectRequest[C]) -> S:
        """Fetch the plugin's snapshot: everything, or with `req.full` False just what its
        change checks said moved (`req.subjects`). Runs in parallel with other plugins' and
        may raise; the error is shown and the last good snapshot kept. It has five minutes
        (`refresh.COLLECT_TIMEOUT_SECONDS`): past that, the refresh records the plugin as
        failed and goes on, and leaves it out of refreshes until its collect returns (a thread
        can't be stopped)."""
        raise NotImplementedError(f"{self.name} has a snapshot model but no collect")

    def store(self, req: StoreRequest[C, S]) -> None:
        """Keep what `collect` fetched in the plugin's tables, replacing what the last good
        collect kept, in one transaction."""
        raise NotImplementedError(f"{self.name} has a snapshot model but no store")

    def load(self, req: LoadRequest[C]) -> S | None:
        """What the last good collect kept, for `entities` and `panels`; None when there's
        nothing (no snapshot model, say). Core holds the database while it runs
        (`Database.reading`), so a refresh can't store between its queries: it mustn't wait on
        another thread using `ctx.db`."""
        return None

    def events(self, req: EventsRequest[C, S]) -> list[Event]:
        """What happened since the last refresh, as events of its `notification_kinds`:
        compare its snapshot (`before`, `after`) and what other plugins' entities say
        (`previous_graph`, `graph`) before and after it, and say what became true. Asked after
        every refresh, of anything, when I've subscribed to one of its kinds, but never before
        the plugin has stored a snapshot to compare with. Say it only when the state changed
        (not mergeable to mergeable), and never about something new in `after` that `before`
        hasn't any state for. A failure is logged and the refresh goes on."""
        return []

    def collect_after(self, req: CollectAfterRequest[C]) -> S:
        """Fetch the snapshot when what to fetch comes from other plugins' entities, e.g. the
        repos git found, in place of `collect`: a plugin that implements it collects after the
        graph. Runs once the other plugins due have stored their snapshots, in parallel with
        other `collect_after`s, and fails like `collect`. It's refreshed on its own schedule,
        not with the plugins it reads: to refresh soon after they change, `watches` them."""
        raise NotImplementedError(f"{self.name} doesn't collect after the graph")

    def after_collect(self, req: AfterCollectRequest[C]) -> None:
        """Work that needs other plugins' entities, once every snapshot is stored, e.g.
        caching lookups in the plugin's tables."""

    def changed(self, req: ChangedRequest[C]) -> Changed:
        """What moved in the plugin's tool, by a check far cheaper than `collect` (files'
        times, a request answered "not modified", other plugins' entities in `graph`, built
        from what's stored), so the plugin refreshes soon after a change rather than at its
        next `refresh_seconds`. Say it as a `fingerprint` of the tool's state, which core
        compares with the last (`req.fingerprint`), the `subjects` that moved, or that
        something moved that no subject names (`everything`); or more than one of these.
        Other plugins' entities are compared with `previous_graph`, which core keeps too. Say
        why in `reason`, for the log; `Changed()` says nothing moved.

        Core asks again as each collect of the plugin starts, so what moves while it runs is
        compared with what it read, and gives `collect` what moved since a collect last
        stored. It asks for a refresh only when something new moved: a different
        fingerprint, a subject not yet pending, or `everything`. May raise; the error is
        logged and the next check tries again."""
        return Changed()

    # Reading

    def refresh_subjects(self, req: SubjectsRequest[C, S]) -> Subjects[S] | None:
        """Fetch just these of its entities again, now (an item I opened, a PR an action
        touched), and fold them into its snapshot, rather than fetching everything. Core
        keeps the result as a refresh would, events and all, but it doesn't count as the
        plugin's refresh (`fetched_at` stays). None, by default, when the plugin can't:
        core refreshes the whole plugin instead, as it does when this fails or takes longer
        than a minute (`refresh.SUBJECTS_TIMEOUT_SECONDS`)."""
        return None

    def relate(self, req: RelateRequest[C]) -> list[tuple[Ref, Ref]]:
        """Relations the plugin knows between one of its entities and another plugin's,
        which neither's own data says: github relates each pull request to the local
        `git.branch` it comes from. Each `(mine, theirs)` becomes a ref from `mine` to
        `theirs`, so `graph.related(theirs, kind)` finds `mine`. Core asks every time it
        builds the graph, so it must be cheap: no I/O."""
        return []

    def linking_patterns(self, req: LinkingRequest[C]) -> dict[str, str | None]:
        """Its `linking` kinds with the patterns of their ids as of now, e.g. only the project
        keys jira has seen; None where no id can be found in text, as by default. Read from
        its tables, so cheap: core asks every time it builds the graph."""
        return {kind: None for kind, spec in self.kinds.items() if spec.linking}

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

    def handle_for(self, req: HandleRequest[C]) -> str | None:
        """The `handle` of an entity of a kind the plugin knows, by id, even one not in the
        graph, e.g. a ticket's key from its URL."""
        return None

    # Page

    def panels(self, req: PanelsRequest[C, S]) -> list[Panel]:
        return []

    def act_now(self, req: ActNowRequest[C, S]) -> list[Item]:
        """The plugin's candidates for core's Act now, each with `signals`, from its
        `panels`. By default the items of the panels marked `act_now` that aren't `deferred`,
        each taking its panel's signals if it has none of its own; a plugin that knows
        better nominates precisely (a deferred item too). Each candidate is about something in
        one of its panels: the tag view offers the tags its panels carry, so a candidate about
        anything else would have its tags missing from the choices once one is selected."""
        return [
            item if item.signals else item.model_copy(update={"signals": panel.signals})
            for panel in req.panels
            if panel.act_now
            for item in panel.items
            if not item.deferred
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
        """Extra pages by key, served at `/<plugin>/<key>` (`page_path`): "ping" is
        `/slack/ping`, and "" the plugin's own `/<plugin>`."""
        return {}

    # Writing

    def actions(self) -> dict[str, ActionHandler]:
        """Button handlers by name, posted to `/actions/<plugin>/<name>`."""
        return {}

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        """Add the arguments of `mfd <name>` to `parser` and return what runs it. Only called
        when `cli_help` is set."""
        raise NotImplementedError(f"{self.name} has cli_help but no cli")

    def import_dir(self, req: ImportRequest[C]) -> list[str]:
        """Read what the plugin recognises in `req.dir` (`mfd import`) into its tables, and
        say what it read, one line each; nothing when it recognises nothing there. What it
        won't overwrite without `req.replace` it skips, saying so in a line. Raises
        `ValueError` or `OSError` when it fails, saying why."""
        return []

    # Set-up and upkeep

    def init_suggested(self) -> bool:
        """Whether `mfd init` offers to set the plugin up by default: not unless the plugin
        says so, as most need something of mine (a site, a mailbox) to be of use."""
        return False

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        """Ask for the `[name]` section's settings in `mfd init`; returns its values (a
        table's as a dict, e.g. `{"teams": {...}}` for `[name.teams]`). `setup` has what
        init knows so far; a plugin can add to its `teams`."""
        return {}

    def init_shown(self, section: Mapping[str, object]) -> dict[str, object]:
        """Optional keys `mfd init` writes, commented out, with these values in place of their
        defaults, under a section of these `section` values (`init_questions`' answers, or
        `init_example`'s for a section left out): where an example says more than the
        default (github's `merge`), or the default is None, which TOML can't write (such a key
        not here isn't written). Uncommenting one should be right for that section: mail
        shows `auth_servers` as the host's known ones, or `[]`, which is what unset means for
        another host."""
        return {}

    def prune(self, req: PruneRequest[C]) -> list[str]:
        """Daily: drop the plugin's data about work finished over `prune_days` ago. Returns
        what was done, one line each."""
        return []


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
