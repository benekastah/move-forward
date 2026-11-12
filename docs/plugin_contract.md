# The plugin contract

The plugin contract in one place: the class variables and hooks of `move_forward.api.Plugin`,
the requests its hooks are given, its context (`ctx`), what `changed` answers, and the markers
its config model can carry. Other types hooks and handlers return or name (`Snapshots`,
`Address`, `Panel`, `Entity`, `Event`, `ActionResult`, `Response` and the like) are documented
on themselves, in `move_forward.api`. Each line says what the code's docstring says; the
docstrings have the detail.
[writing-a-plugin.md](writing-a-plugin.md) walks through making a plugin with these, and
`tests/test_plugin_contract_doc.py` checks that this page names every one of them, and nothing
that isn't.

`Plugin[C, S]`: `C` is the model of the plugin's config section, `S` its snapshot's (`BaseModel`
for a plugin that collects nothing). Every hook has a default, so a plugin overrides only what
it uses: most do nothing, `collect` and `cli` exist only when overridden, and the rows say what
the others do.

## Class variables

### Identity

| Name | Type, default | What it says |
| --- | --- | --- |
| `name` | `str`, required | e.g. `github`: the config section, snapshot source, table prefix and URL segment. A lower-case letter, then lower-case letters, digits and `_`; no other plugin's, none of core's (`registry.RESERVED_NAMES`), and no prefix of core's tables or indexes (`team` of `team_channels`); the registry refuses it otherwise. |
| `title` | `str`, required | e.g. `GitHub`: the heading of the plugin's group on the page. |
| `config_model` | `type[BaseModel]`, required | The model of the `[name]` section: `C`. Make it a `Strict` model, which rejects unknown keys (nothing checks this). The registry refuses a plugin without one. |
| `api_version` | `int \| None`, `None` | The `API_VERSION` it was built against; one built against another is left out, said why in `mfd check` and the page's footer. |

### What it publishes and depends on

| Name | Type, default | What it says |
| --- | --- | --- |
| `kinds` | `dict[str, KindSpec]`, `{}` | The entity kinds it publishes, each with its spec: its attributes' model, which the graph checks entities against, and its flags (`linking`, `hides`, `fetchable`, `gone_means_done`, `resolvable`, `tags_from_text`). A kind is named `<name>.<x>` or for a family (core's, or one in `families`); its attributes are a `KindModel`, and a family kind's extend the family's model. |
| `families` | `tuple[Family, ...]`, `()` | Families of kinds it declares, beyond core's pull request, ticket and person: the model its kinds extend and the rule its ids follow. One declaring a family differently from core or another plugin is left out. |
| `requires` | `list[str]`, `[]` | Plugins whose entities it's built on (`["git"]`); the config must enable them too. |
| `migrations` | `list[Migration]`, `[]` | Scripts creating and changing its tables (prefixed with `name`), applied in order. Append only. |

### Scheduling

| Name | Type, default | What it says |
| --- | --- | --- |
| `change_check_seconds` | `int \| None`, `None` | How often `mfd serve` asks `changed`; None for no poll. In `mfd serve`, `changed` is also asked as each of its collects starts, when this is set or it `watches` another. |
| `watches` | `list[str]`, `[]` | Plugins whose entities its `changed` reads from the graph (git_stack: `["git"]`). The settle step: in each refresh that collects one of them, once they've stored, `changed` is asked with the new graph, and the plugin is collected (again, if it collected in that refresh from the graph before) when it says something moved, in the same refresh (`refresh._settle`). |

### Notifications and setup

| Name | Type, default | What it says |
| --- | --- | --- |
| `notification_kinds` | `dict[str, str]`, `{}` | The events its `events` makes, by kind, each with what it says, for the settings page and `mfd init`: `pr_mergeable` is subscribed to as `github.pr_mergeable`. |
| `always_on` | `bool`, `False` | On without a section (notes), so `mfd init` doesn't offer it. |
| `init` | `Init`, `Init()` | How `mfd init` sets the section up (below). By default nothing is asked and it isn't suggested. |

### Derived

| Name | Type | What it says |
| --- | --- | --- |
| `collects` | `bool` (property) | Whether it fetches anything: it overrides `collect` (and so `snapshots`; the registry refuses one without the other), so a refresh calls its `collect` and it has a schedule. |

## `Init`

Read only by `mfd init` and `mfd init --example`; built in the plugin's `setup.py`.

| Field | Type, default | What it says |
| --- | --- | --- |
| `questions` | `(Asker, InitSetup) -> InitSection`, asks nothing | Asks for the section's settings when the plugin is chosen; `InitSetup` has what init knows so far, and a plugin can add to its `teams`. |
| `example` | `InitSection`, empty | Example values for the required settings (and its `shown`): written commented out for a plugin not chosen, uncommented by `--example` for one `example_on`. |
| `tables` | `str`, `""` | Commented-out tables the model can't show, written after the optional keys (`[github.teams]`, `[mail.<name>]`). |
| `suggested` | `bool`, `False` | Whether `mfd init` offers to set it up by default. |
| `example_on` | `bool`, `False` | `mfd init --example` writes the section on, with `example`'s values, rather than commented out. |
| `finds_teams` | `bool`, `False` | Its `questions` set `setup.teams` (github's), so core doesn't ask for them. |
| `token_help` | `str`, `""` | `mfd init`'s next step for its tokens, in place of the plain "`mfd token set <name>` stores its token". |

## Hooks

### Called before there's a context

| Hook | Signature | Purpose, and when core calls it |
| --- | --- | --- |
| `tokens` | `tokens(config: C) -> dict[str, TokenSettings]` | Every token it keeps, by name (`""` for its own, one per account otherwise), for `mfd token` and loading the config. By default its section, when that has `token_env` and `keychain_service`. |
| `teams` | `teams(config: C) -> set[str]` | The top-level teams its section names, which loading the config checks the top level has. Empty by default. |
| `cli` | `cli(parser: ArgumentParser) -> CliHandler` | Adds the arguments of `mfd <name>` and returns what runs it, given an `InputRequest` with `args`. It has a command only when it overrides this; the override's docstring's first line is the command's help. |
| `pages` | `pages() -> dict[str, PageHandler]` | Extra pages by key, served at `/<plugin>/<key>` (`""`: `/<plugin>`), each given an `InputRequest` with `page`. |
| `actions` | `actions() -> dict[str, ActionHandler]` | Button handlers by name, posted to `/actions/<plugin>/<name>`, each given an `InputRequest` with `form`. |

### Refresh

| Hook | Signature | Purpose, and when core calls it |
| --- | --- | --- |
| `collect` | `collect(req: RefreshRequest[C]) -> S` | Fetches its snapshot: everything, or with `req.full` False just `req.subjects`. Called in each refresh it's due in (all of them, or one naming it or its entities), unless an earlier collect is still running, in parallel with the others', from the graph as stored before the refresh (or again in the settle step, see `watches`). Five minutes, then it's recorded as failed. |
| `snapshots` | `snapshots(req: ContextRequest[C]) -> Snapshots[S]` | Where it keeps what `collect` fetched, in its own tables (`save`, `load`). Overridden together with `collect`. |
| `changed` | `changed(req: RefreshRequest[C]) -> Changed` | A cheap check of what moved in its tool (a `Changed`, below), so it refreshes soon. Asked by `mfd serve` every `change_check_seconds` and as each of its collects starts (when it has a `change_check_seconds` or `watches` another), and in the settle step of a refresh of a plugin it `watches`. |
| `events` | `events(req: Request[C, S]) -> list[Event]` | What became true since the last refresh, as events of its `notification_kinds`, comparing `req.previous` with now. Asked after every refresh when I've subscribed to one of its kinds, once it has stored a snapshot. |
| `after_collect` | `after_collect(req: Request[C, S]) -> None` | Work that needs other plugins' entities (caching lookups in its tables). After each refresh's events and daily prune: for the plugins due in it (even one still collecting), those collected again in the settle step, and every plugin that collects nothing; not while the dashboard stops. It reads only what's stored. |

### Graph

| Hook | Signature | Purpose, and when core calls it |
| --- | --- | --- |
| `entities` | `entities(req: Request[C, S]) -> list[Entity]` | What it publishes to the graph, from its snapshot and tables. Called every time the graph is built (`req.graph` empty). |
| `relate` | `relate(req: Request[C, S]) -> list[tuple[Ref, Ref]]` | Relations between one of its entities and another plugin's that neither's data says (a PR and its local branch). Every time the graph is built: no I/O. |
| `linking_patterns` | `linking_patterns(req: ContextRequest[C]) -> dict[str, str \| None]` | Its `linking` kinds with the patterns of their ids now. Every time the graph is built, so read from its tables. |
| `fetch` | `fetch(req: RefRequest[C]) -> list[Entity]` | Entities of one of its kinds, by id, fetched from its tool now, even ones its snapshot doesn't hold, for `Graph.fetch`. |
| `address` | `address(req: RefRequest[C]) -> dict[str, Address]` | Where each entity of one of its kinds is (`url`) and what text names it (`handle`), by id, even one not in the graph. By default the id as the URL for a family whose ids are URLs. |

### Page

| Hook | Signature | Purpose, and when core calls it |
| --- | --- | --- |
| `panels` | `panels(req: Request[C, S]) -> list[Panel]` | Its panels, from its snapshot and the graph: pure, no I/O. Called for each page, and after each refresh for a plugin I've subscribed to some event of (to find items newly shown with `notify`). |
| `item_actions` | `item_actions(req: Request[C, S]) -> ItemAdditionsFor` | What it adds to other plugins' items (buttons, badges, details, links). Called once per page; the function it returns once per item, with an `ItemRequest`. |

### Upkeep

| Hook | Signature | Purpose, and when core calls it |
| --- | --- | --- |
| `prune` | `prune(req: Request[C, S]) -> list[str]` | Daily: drops its data about work finished over `prune_days` ago, and says what it did. |

## Requests

Every hook with a context takes one request; a field a hook isn't given keeps its default.

### `ContextRequest`

For `snapshots` and `linking_patterns`.

| Field | Type | What it holds |
| --- | --- | --- |
| `ctx` | `Context[C]` | The plugin's context: its section, storage and the settings every plugin shares. |

### `Request`

For `entities`, `relate`, `panels`, `item_actions`, `after_collect`, `prune` and `events`.

| Field | Type | What it holds |
| --- | --- | --- |
| `ctx` | `Context[C]` | The plugin's context. |
| `graph` | `Graph` | Every plugin's entities; empty for `entities`, and without relations for `relate`. |
| `snapshot` | `S \| None` | What it last stored (for `events`, the snapshot now); None before its first refresh. |
| `previous` | `Previous[S]` | For `events` only: its snapshot before this refresh, when that was stored, and the graph then. |

### `RefreshRequest`

For `collect` and `changed`.

| Field | Type | What it holds |
| --- | --- | --- |
| `ctx` | `Context[C]` | The plugin's context. |
| `graph` | `Graph` | For `collect`, as stored before the refresh (in the settle step, as the plugins it watches just stored it); for `changed`, as stored when it's asked. |
| `full` | `bool` | For `collect`: whether to fetch everything (its first collect, its `refresh_seconds` passed, something moved no subject names, or nothing named). |
| `subjects` | `frozenset[str]` | For `collect`: what its checks said moved since a collect last stored, and the ids of its entities asked for again. |
| `previous` | `Previous[BaseModel]` | For `changed` only: when its last check ran, what it answered, and the graph it was given. |

### `RefRequest`

For `address` and `fetch`.

| Field | Type | What it holds |
| --- | --- | --- |
| `ctx` | `Context[C]` | The plugin's context. |
| `kind` | `str` | One of its `kinds`. |
| `ids` | `list[str]` | The entities asked about. |

### `InputRequest`

For pages, buttons and `mfd <plugin>`.

| Field | Type | What it holds |
| --- | --- | --- |
| `ctx` | `Context[C]` | The plugin's context. |
| `graph` | `Graph` | Every plugin's entities. |
| `page` | `Page` | For a page: its `query` and `path`. |
| `form` | `Form` | For a button: its `fields` (untrusted), `surface` and `back`. `req.field(name)` reads a required one. |
| `args` | `Namespace` | For `mfd <plugin>`: its parsed arguments. |

### `Previous`

What held before, for `changed` and `events`.

| Field | Type | What it holds |
| --- | --- | --- |
| `at` | `datetime \| None` | For `changed`, when its last check ran; for `events`, when the refresh that stored `snapshot` began. |
| `fingerprint` | `str \| None` | For `changed`: what its last check answered. |
| `snapshot` | `S \| None` | For `events`: its snapshot before this refresh. |
| `graph` | `Graph \| None` | The graph at its last check that didn't raise (`changed`): None at the first since the dashboard or `mfd refresh` started, except in the settle step, which gives the graph before the refresh. Or the graph before this refresh (`events`). |

### `ItemRequest`

What the function `item_actions` returns is called with, once per item.

| Field | Type | What it holds |
| --- | --- | --- |
| `item` | `Item` | The item. |
| `owner` | `str` | The plugin whose item it is. |
| `surface` | `Surface` | Where it's shown: the dashboard, focus mode or Act now. |

## Context and answers

### `Context`

`req.ctx`, in every request: what a plugin gets to work with.

| Field | Type | What it holds |
| --- | --- | --- |
| `config` | `C` | Its `[name]` section. |
| `email` | `str` | My work email. |
| `teams` | `list[str]` | The names of the teams I'm on or review for. |
| `prune_days` | `int` | Work finished this long ago is cleared by `prune`. |
| `now` | `datetime` | Now, aware, in UTC; shown, or a day taken from it, in local time. |
| `db` | `Database` | For its own repository over its own tables. |
| `kv` | `KvRepository` | Key/value state in its namespace. |
| `name` | `str` | Its name, as `Plugin.name`. |
| `due_horizon` | `timedelta` | How far ahead a due date starts to count (`[act_now] due_warning_days`), for `due_pressure`. |
| `tokens` | `Mapping[str, TokenSettings] \| None` | Where each of its tokens is, as `Plugin.tokens` says, for `token`; None for its default, the section's own when it says where a token is. |

### `Context`'s methods

| Name | What it does |
| --- | --- |
| `log` | Its logger, `move_forward.plugins.<name>`. |
| `today` | Today where I am: the local date. |
| `part(name)` | The source a part of its refresh is recorded as (`github.derived`). |
| `problem(key, text)` | Reports something wrong that isn't its refresh failing, or with None that it's gone; shown with its state on the page, kept across restarts. |
| `token(account=None, *, secret="token")` | Its token, or an account's: from its `token_env`, else the secret store. Raises `SourceError` saying how to give one. |

### `Changed`

What `changed` answers. `Changed()` says nothing moved; core asks for a refresh when the
fingerprint differs from the last, any subject is named, or `everything`.

| Field | Type | What it holds |
| --- | --- | --- |
| `fingerprint` | `str \| None` | The tool's state now, as text that changes whenever it moves; core compares the next with it. |
| `subjects` | `frozenset[str]` | What moved since the last check, in its own terms, for `collect` to fetch just those. |
| `everything` | `bool` | Something moved that no subject names, so the next collect is a full one. |
| `reason` | `str` | What changed, for the log. |

## Config markers

A config model marks a field in its type, `Annotated[T, MARKER]`. A marker that would mark
nothing is refused, as a `Strict` class is defined and, for any plugin's config model, again at
registration: one given as a default, or one buried inside the type (`Annotated[str, LOCKED] |
None`, `list[...]`, a `type` alias, generic or not).

| Marker | What it says |
| --- | --- |
| `LOCKED` | The settings page shows the field but won't change it: it says where a credential goes, where items link with my login in the link, whose word decides a sender is who they claim, or what runs where. A section's `token_env` and `keychain_service` are locked without it. |
| `TABLES` | The field's entries are tables of their own, by name (mail's `[mail.<name>]`): `mfd init` leaves it to the example's `tables`. |
