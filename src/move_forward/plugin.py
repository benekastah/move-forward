"""The plugin protocol: what core asks of each external tool's plugin.

A plugin owns one config section, one snapshot (what it fetches on each refresh), its own
tables, and the panels, actions, pages and commands for its tool. Plugins never see each
other's config, snapshots or tables; they learn about each other's data only through the
entity graph (`graph`). Every method has a do-nothing default, so a plugin implements only
what it uses.
"""

import argparse
import inspect
import logging
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from . import keychain, localtime, shell
from .act_now import DUE_HORIZON
from .asking import Init
from .db import Database, Migration
from .graph import Entity, Graph, KindSpec, Ref
from .keychain import TokenSettings
from .kinds import FAMILIES, Family, is_url
from .notifications import Event
from .page import Item, ItemAdditions, Panel, Surface
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
    due_horizon: timedelta = DUE_HORIZON
    """How far ahead a due date starts to count, as `[act_now] due_warning_days` says; a plugin
    making items candidates for Act now by their due dates passes it to `due_pressure`."""
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
class ContextRequest[C: BaseModel]:
    """A hook given only the plugin's context: `snapshots` and `linking_patterns`."""

    ctx: Context[C]


@dataclass(frozen=True)
class Previous[S: BaseModel]:
    """What held before, for `changed` and `events` to compare with now."""

    at: datetime | None = None
    """For `changed`, when the plugin's last change check ran; for `events`, when the refresh
    that last stored a snapshot began (a refresh, or a refresh of subjects), the one `snapshot`
    holds: a refresh that failed stored nothing, so it doesn't count. None before the first."""
    fingerprint: str | None = None
    """What the plugin's last change check answered (`Changed.fingerprint`), for `changed`;
    None before the first."""
    snapshot: S | None = None
    """The plugin's snapshot before this refresh, for `events`: after a start, what was stored
    when the dashboard stopped."""
    graph: Graph | None = None
    """The graph then: at the plugin's last change check that didn't raise (`changed`), or
    before this refresh (`events`). For `changed`, None at the first check since the dashboard
    (or `mfd refresh`) started, as it's the one thing a restart can't rebuild; except in a
    refresh's settle step, which gives the graph from before the refresh (see `watches`)."""


@dataclass(frozen=True)
class Request[C: BaseModel, S: BaseModel]:
    """A hook working from what's stored: `entities`, `relate`, `panels`, `item_actions`,
    `after_collect`, `prune` and `events`."""

    ctx: Context[C]
    graph: Graph
    """Every plugin's entities. Empty for `entities`, which the graph is built from, and
    before any relations for `relate`."""
    snapshot: S | None
    """What the plugin last stored; None before its first refresh. For `events`, the snapshot
    now."""
    previous: Previous[S] = field(default_factory=Previous[S])
    """For `events` only: the plugin's snapshot before this refresh, when the refresh that
    stored it began (`at`), and the graph then."""


@dataclass(frozen=True)
class RefreshRequest[C: BaseModel]:
    """A hook reading the plugin's tool: `collect` and `changed`."""

    ctx: Context[C]
    graph: Graph
    """Every plugin's entities, as stored: for `collect`, as stored before the refresh began,
    or, for a plugin collected again because one it `watches` just stored, as that one stored
    it; for `changed`, as stored when it's asked."""
    full: bool = True
    """For `collect`: whether to fetch everything: at the plugin's first collect since the
    dashboard started, unless it collects only entities asked for (see `subjects`) and a full
    collect stored within its `refresh_seconds`; when its `refresh_seconds` have passed since
    the last full collect that stored; when its change checks saw something move that no
    subject names; and when they named nothing. Otherwise fetching `subjects` is enough,
    keeping the rest as stored. `changed` isn't told; it gets the default, which means nothing
    to it."""
    subjects: frozenset[str] = frozenset()
    """For `collect`: what moved, in the plugin's own terms. What its change checks said
    (`Changed.subjects`) since a collect last stored: core keeps each until a collect that
    started after it was said stores, so a collect that fails loses none of it, and what moves
    again while one runs stays for the next. With them, the ids of its entities asked to be
    fetched again now: those an action touched (`ActionResult.touched`), or that I opened or
    asked for again (↻, `mfd refresh --subject`). A collect that can't fetch just those
    fetches everything. Empty for `changed`, which says what moved rather than
    being told."""
    previous: Previous[BaseModel] = field(default_factory=Previous[BaseModel])
    """For `changed` only: when core took the plugin's last answer, its fingerprint, and the
    graph it was given, to compare with `graph` for what other plugins' refreshes moved since
    (a plugin that `watches` them)."""


@dataclass(frozen=True)
class RefRequest[C: BaseModel]:
    """A hook asked about some of the plugin's entities by id: `address` and `fetch`."""

    ctx: Context[C]
    kind: str
    """One of the plugin's `kinds` (of a family, each publisher's)."""
    ids: list[str]


@dataclass(frozen=True)
class Address:
    """Where an entity is and what it's called, as `Plugin.address` knows them."""

    url: str | None = None
    """A link to it, e.g. a ticket's from its key; None when the plugin can't say."""
    handle: str | None = None
    """What text names it by (its family's `handle`), e.g. a ticket's key from its URL; None
    when the plugin can't say."""


@dataclass(frozen=True)
class Page:
    """A page asked for (`Plugin.pages`)."""

    query: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    path: str = ""
    """The page's path, e.g. `/slack/ping`."""


@dataclass(frozen=True)
class Form:
    """A button's form posted (`Plugin.actions`)."""

    fields: dict[str, str] = field(default_factory=dict[str, str])
    """The form's fields: untrusted, as anything a form sends."""
    surface: Surface = "dashboard"
    """Where the button was: the dashboard or focus mode."""
    back: str | None = None
    """The page the button was on, when it wasn't the dashboard (a focus page)."""


@dataclass(frozen=True)
class InputRequest[C: BaseModel]:
    """A hook answering what I asked for: a page (`pages`), a button (`actions`) or `mfd
    <plugin>` (`cli`)."""

    ctx: Context[C]
    graph: Graph
    page: Page = field(default_factory=Page)
    """For a page only."""
    form: Form = field(default_factory=Form)
    """For a button only."""
    args: argparse.Namespace = field(default_factory=argparse.Namespace)
    """For `mfd <plugin>` only: its parsed arguments."""

    def field(self, name: str) -> str:
        """A required field of the form; `ActionError` saying "missing field" when it lacks
        it."""
        try:
            return self.form.fields[name]
        except KeyError:
            raise ActionError(f"missing field {name!r}") from None


@dataclass(frozen=True)
class ItemRequest:
    """An item the function `item_actions` returned is asked about."""

    item: Item
    owner: str
    """The plugin whose item it is."""
    surface: Surface
    """Where it's shown: the dashboard, focus mode or Act now."""


ItemAdditionsFor = Callable[[ItemRequest], ItemAdditions]
"""What `Plugin.item_actions` returns: what the plugin adds to each item of one page."""


def adds_nothing(req: ItemRequest) -> ItemAdditions:
    """What `Plugin.item_actions` returns by default: nothing added to any item."""
    return ItemAdditions()


@dataclass(frozen=True, kw_only=True)
class Changed:
    """`changed`'s answer: what moved in the plugin's tool, which core keeps (see
    `Plugin.changed`). `Changed()` says nothing moved."""

    fingerprint: str | None = None
    """The tool's state now, as text that changes whenever it moves (a digest, a mailbox's
    counts); None for a plugin that has none. Core keeps it and compares the next with it."""
    subjects: frozenset[str] = frozenset()
    """What moved since the last check, in the plugin's own terms (repos, PRs), for
    `collect` to fetch just those (`RefreshRequest.subjects`). Each asks for a refresh, so
    name a subject only when it moved again, not while it's still as the last check saw it."""
    everything: bool = False
    """Something moved that `subjects` doesn't name, so the next collect is a full one."""
    reason: str = ""
    """What changed, e.g. "notifications about 2 PRs"; it goes into the log line."""


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


PageHandler = Callable[[InputRequest[Any]], str | Response | None]
"""Renders a page, as HTML or a `Response`; None means not found."""
CliHandler = Callable[[InputRequest[Any]], int]
"""Runs `mfd <plugin> ...` with the parsed arguments; returns the exit status."""
API_VERSION = 2
"""The version of `move_forward.api` this dashboard offers. A change that would break a
plugin built against it (a hook's request losing a field, a name leaving `api`) raises it;
see `docs/api-changes.md`."""


@dataclass(frozen=True)
class ActionResult:
    """What an action did: the notice for the page, and what else it changed."""

    notice: str
    touched: list[Ref] = field(default_factory=list[Ref])
    """What the action changed outside its own plugin's tool, e.g. a PR it reacted to: the
    server refreshes the plugin that publishes each, its collect given their ids, as well as
    the acting plugin, which is refreshed as it would have been even when it publishes them."""


class ActionError(Exception):
    """Something the user needs to know; shown on the page, not a crash."""


ActionHandler = Callable[[InputRequest[Any]], str | ActionResult]
"""Runs a button's action; returns the notice for the page (or a result with redirect
parameters too) and raises `ActionError` with the reason when it can't."""


class Snapshots[S: BaseModel](Protocol):
    """Where a plugin keeps its snapshot (`Plugin.snapshots`): usually its repository over the
    tables its migrations made. Nothing is kept as JSON."""

    def save(self, snapshot: S) -> None:
        """Keep what `collect` fetched, replacing what the last good collect kept, in one
        transaction."""
        ...

    def load(self) -> S | None:
        """What the last good collect kept, for `entities` and `panels`; None when there's
        nothing. Core holds the database while it runs (`Database.reading`), so a refresh can't
        store between its queries: it mustn't wait on another thread using `ctx.db`."""
        ...


class _NoSnapshots[S: BaseModel]:
    """What a plugin that doesn't collect keeps: nothing."""

    def __init__(self, plugin: str):
        self._plugin = plugin

    def save(self, snapshot: S) -> None:
        raise NotImplementedError(f"{self._plugin} keeps no snapshot")

    def load(self) -> S | None:
        return None


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
    api_version: ClassVar[int | None] = None
    """The `API_VERSION` the plugin was built against: one built against another is left
    out, with the reason in `mfd check` and the page's footer."""
    notification_kinds: ClassVar[dict[str, str]] = {}
    """The events the plugin's `events` makes, by kind, each with what it says, for the
    settings page and `mfd init` to offer: `pr_mergeable` is stored and subscribed to as
    `github.pr_mergeable`."""
    change_check_seconds: ClassVar[int | None] = None
    """How often `mfd serve` asks `changed`; None for a plugin without such a check. In `mfd
    serve`, it's also asked as each of its collects starts, when this is set or it `watches`
    another."""
    watches: ClassVar[list[str]] = []
    """Plugins whose data this one's `changed` reads from the graph, e.g. `["git"]` for
    git_stack, whose `collect` needs the repos git found: in each refresh that collects one of
    them, once they've stored, `changed` is asked with the new graph, and the plugin collected
    (again, if it was collected in that refresh, from the graph as it was before) when it says
    something moved, rather than waiting for `change_check_seconds` (see `refresh._settle`)."""
    always_on: ClassVar[bool] = False
    """On without a section (notes), so `mfd init` doesn't offer it."""
    init: ClassVar[Init] = Init()
    """How `mfd init` sets the section up: its questions, example and the like (`Init`). By
    default nothing is asked and the plugin isn't suggested."""

    @property
    def collects(self) -> bool:
        """Whether the plugin fetches anything: it overrides `collect` (and so `snapshots`, as
        the registry checks), so a refresh calls its `collect` and it has a schedule."""
        return overrides(self, "collect")

    def tokens(self, config: C) -> dict[str, TokenSettings]:
        """Every token the plugin keeps, for `mfd token`, by name: `""` for its own, and one per
        account for a plugin reading several (several mailboxes, say), stored as
        `<plugin>.<name>`. By default its section is its own token's place, when that has
        `token_env` and `keychain_service`. Each needs a `keychain_service` of its own: the
        config is refused when two tokens share one. Its context's `token` reads them."""
        return _section_token(config)

    def teams(self, config: C) -> set[str]:
        """The top-level teams (`Context.teams`, by name) the plugin's section names, e.g. those
        github's `[github.teams]` gives a slug for, so the config refuses a team the top level
        doesn't have. Empty by default: core knows nothing of how a section keeps them."""
        return set()

    # Refresh

    def collect(self, req: RefreshRequest[C]) -> S:
        """Fetch the plugin's snapshot: everything, or with `req.full` False just what its
        change checks said moved (`req.subjects`), from the graph (`req.graph`: as stored
        before the refresh, or after for a plugin collected again as it `watches` one that
        refreshed). Runs in parallel with other plugins' and
        may raise; the error is shown and the last good snapshot kept. It has five minutes
        (`refresh.COLLECT_TIMEOUT_SECONDS`): past that, the refresh records the plugin as
        failed and goes on, and leaves it out of refreshes until its collect returns (a thread
        can't be stopped)."""
        raise NotImplementedError(f"{self.name} doesn't collect")

    def snapshots(self, req: ContextRequest[C]) -> "Snapshots[S]":
        """Where the plugin keeps what `collect` fetched, in its own tables: usually its
        repository over them (see `Snapshots`). A plugin overriding `collect` overrides this
        too; by default nothing is kept, for a plugin that doesn't collect."""
        return _NoSnapshots[S](self.name)

    def events(self, req: Request[C, S]) -> list[Event]:
        """What happened since the last refresh, as events of its `notification_kinds`:
        compare its snapshot (`req.previous.snapshot`, `req.snapshot`) and what other plugins'
        entities say (`req.previous.graph`, `req.graph`) before and after it, and say what
        became true. Asked after every refresh, of anything, when I've subscribed to one of its
        kinds, but never before the plugin has stored a snapshot to compare with (so neither
        snapshot is None). Say it only when the state changed (not mergeable to mergeable), and
        never about something new now that the snapshot before hasn't any state for. A failure
        is logged and the refresh goes on."""
        return []

    def after_collect(self, req: Request[C, S]) -> None:
        """Work that needs other plugins' entities, once every snapshot is stored, e.g.
        caching lookups in the plugin's tables."""

    def changed(self, req: RefreshRequest[C]) -> Changed:
        """What moved in the plugin's tool, by a check far cheaper than `collect` (files'
        times, a request answered "not modified", other plugins' entities in `graph`, built
        from what's stored), so the plugin refreshes soon after a change rather than at its
        next `refresh_seconds`. Say it as a `fingerprint` of the tool's state, which core
        compares with the last (`req.previous.fingerprint`), the `subjects` that moved, or that
        something moved that no subject names (`everything`); or more than one of these.
        Other plugins' entities are compared with `req.previous.graph`, which core keeps too,
        and a tool asked what moved since a time is given `req.previous.at`. Say
        why in `reason`, for the log; `Changed()` says nothing moved.

        In `mfd serve`, core asks again as each collect of the plugin starts (when it has a
        `change_check_seconds` or `watches` another), so what moves while it runs is
        compared with what it read, and gives `collect` what moved since a collect last
        stored. It asks for a refresh whenever something moved since the last check: a
        different fingerprint, any subject (one still pending after a collect that failed
        too), or `everything`. May raise; the error is logged and the next check tries
        again."""
        return Changed()

    # Reading

    def relate(self, req: Request[C, S]) -> list[tuple[Ref, Ref]]:
        """Relations the plugin knows between one of its entities and another plugin's,
        which neither's own data says: github relates each pull request to the local
        `git.branch` it comes from. Each `(mine, theirs)` becomes a ref from `mine` to
        `theirs`, so `graph.related(theirs, kind)` finds `mine`. Core asks every time it
        builds the graph, so it must be cheap: no I/O."""
        return []

    def linking_patterns(self, req: ContextRequest[C]) -> dict[str, str | None]:
        """Its `linking` kinds with the patterns of their ids as of now, e.g. only the project
        keys jira has seen; None where no id can be found in text, as by default. Read from
        its tables, so cheap: core asks every time it builds the graph."""
        return {kind: None for kind, spec in self.kinds.items() if spec.linking}

    def entities(self, req: Request[C, S]) -> list[Entity]:
        """What the plugin publishes to the graph, from its snapshot and tables."""
        return []

    def fetch(self, req: RefRequest[C]) -> list[Entity]:
        """Entities of one of its `kinds`, by id, fetched from its tool now, even ones its
        snapshot doesn't hold; `Graph.fetch` asks. Core asks only about the plugin's own kinds
        (about a family, each plugin that publishes it), so `req.kind` is one of them."""
        return []

    def address(self, req: RefRequest[C]) -> dict[str, Address]:
        """Where each of these entities of one of its `kinds` is, and what it's called, by id,
        even one not in the graph: a ticket's URL from its key, its key from its URL. Core asks
        only about the plugin's own kinds. By default, its URL is its id where that's a URL of a
        family whose ids are URLs (a pull request's), and nothing else is known."""
        family = FAMILIES.get(req.kind)
        return {
            id: Address(url=id)
            for id in req.ids
            if family is not None and family.id_rule(id) and is_url(id)
        }

    # Page

    def panels(self, req: Request[C, S]) -> list[Panel]:
        return []

    def item_actions(self, req: Request[C, S]) -> ItemAdditionsFor:
        """What the plugin adds to other plugins' items on one page: buttons (e.g. "add todo"
        on a PR), badges, details and links. Core calls it once per page and the function it
        returns once per item, saying whose item it is and where it's shown (`ItemRequest`):
        what needs the plugin's tables or a pass over the graph is done here, once, not per
        item. Adds nothing by default."""
        return adds_nothing

    def pages(self) -> dict[str, PageHandler]:
        """Extra pages by key, served at `/<plugin>/<key>` (`page_path`): "ping" is
        `/slack/ping`, and "" the plugin's own `/<plugin>`. Each is given an `InputRequest`
        with the `page` asked for."""
        return {}

    # Writing

    def actions(self) -> dict[str, ActionHandler]:
        """Button handlers by name, posted to `/actions/<plugin>/<name>`. Each is given an
        `InputRequest` with the `form` posted."""
        return {}

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        """Add the arguments of `mfd <name>` to `parser` and return what runs it, given an
        `InputRequest` with the parsed `args`. A plugin has a command when it overrides this,
        with a docstring whose first line is the command's help (`command_help`)."""
        raise NotImplementedError(f"{self.name} has no command")

    # Upkeep

    def prune(self, req: Request[C, S]) -> list[str]:
        """Daily: drop the plugin's data about work finished over `prune_days` ago. Returns
        what was done, one line each."""
        return []


def overrides(plugin: Plugin[Any, Any], hook: str) -> bool:
    """Whether `plugin`'s class overrides `Plugin`'s method `hook`: what some hooks' being
    there says (`collects`, `command_help`)."""
    return getattr(type(plugin), hook) is not getattr(Plugin, hook)


def command_help(plugin: Plugin[Any, Any]) -> str | None:
    """The help line of `mfd <name>`: the first line of the docstring of the plugin's `cli`;
    None for a plugin without a command (not overriding `cli`), or whose `cli` has no
    docstring (which the registry refuses). Its own docstring, not one inherited from
    `Plugin.cli`."""
    if not overrides(plugin, "cli"):
        return None
    doc = type(plugin).cli.__doc__
    return inspect.cleandoc(doc).splitlines()[0] if doc and doc.strip() else None


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
