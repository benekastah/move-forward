# The plugin contract

Everything a plugin is to core, in one place: the class variables and hooks of
`move_forward.api.Plugin`, the requests its hooks are given, and the markers its config model can
carry. Each line says what the code's docstring says; the docstrings have the detail.
[writing-a-plugin.md](writing-a-plugin.md) walks through making a plugin with these, and
`tests/test_plugin_contract_doc.py` checks that this page names every one of them, and nothing
that isn't.

`Plugin[C, S]`: `C` is the model of the plugin's config section, `S` its snapshot's (`BaseModel`
for a plugin that collects nothing). Every hook has a do-nothing default, so a plugin overrides
only what it uses.

## Class variables

### Identity

| Name | Type, default | What it says |
| --- | --- | --- |
| `name` | `str`, required | e.g. `github`: the config section, snapshot source, table prefix and URL segment. |
| `title` | `str`, required | e.g. `GitHub`: the heading of the plugin's group on the page. |
| `config_model` | `type[BaseModel]`, required | The model of the `[name]` section: `C`. |
| `api_version` | `int \| None`, `None` | The `API_VERSION` it was built against; one built against another is left out, said why in `mfd check` and the page's footer. |

### What it publishes and depends on

| Name | Type, default | What it says |
| --- | --- | --- |
| `kinds` | `dict[str, KindSpec]`, `{}` | The entity kinds it publishes, each with its spec: its attributes' model, which the graph checks entities against, and its flags (`linking`, `hides`, `fetchable`, `gone_means_done`, `resolvable`, `tags_from_text`). |
| `families` | `tuple[Family, ...]`, `()` | Families of kinds it declares, beyond core's pull request, ticket and person: the model its kinds extend and the rule its ids follow. One declaring a family differently from core or another plugin is left out. |
| `requires` | `list[str]`, `[]` | Plugins whose entities it's built on (`["git"]`); the config must enable them too. |
| `migrations` | `list[Migration]`, `[]` | Scripts creating and changing its tables (prefixed with `name`), applied in order. Append only. |

### Scheduling

| Name | Type, default | What it says |
| --- | --- | --- |
| `change_check_seconds` | `int \| None`, `None` | How often `mfd serve` asks `changed`; None for no poll. `changed` is asked as each collect starts too, whether this is set or the plugin `watches` another. |
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
| `collect` | `collect(req: RefreshRequest[C]) -> S` | Fetches its snapshot: everything, or with `req.full` False just `req.subjects`. Called in each refresh, in parallel with the others', from the graph as stored before it (or again in the settle step, see `watches`). Five minutes, then it's recorded as failed. |
| `snapshots` | `snapshots(req: ContextRequest[C]) -> Snapshots[S]` | Where it keeps what `collect` fetched, in its own tables (`save`, `load`). Overridden together with `collect`. |
| `changed` | `changed(req: RefreshRequest[C]) -> Changed` | A cheap check of what moved in its tool (a `fingerprint`, `subjects`, `everything`), so it refreshes soon. Asked every `change_check_seconds`, as each collect starts, and in the settle step of a refresh of a plugin it `watches`. |
| `events` | `events(req: Request[C, S]) -> list[Event]` | What became true since the last refresh, as events of its `notification_kinds`, comparing `req.previous` with now. Asked after every refresh when I've subscribed to one of its kinds, once it has stored a snapshot. |
| `after_collect` | `after_collect(req: Request[C, S]) -> None` | Work that needs other plugins' entities, once every snapshot is stored (caching lookups in its tables). |

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
| `panels` | `panels(req: Request[C, S]) -> list[Panel]` | Its panels, from its snapshot and the graph: pure, no I/O. Called for each page. |
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
| `graph` | `Graph \| None` | The graph at its last check (`changed`; in a refresh, the graph before it when there was none) or before this refresh (`events`). |

### `ItemRequest`

What the function `item_actions` returns is asked about.

| Field | Type | What it holds |
| --- | --- | --- |
| `item` | `Item` | The item. |
| `owner` | `str` | The plugin whose item it is. |
| `surface` | `Surface` | Where it's shown: the dashboard, focus mode or Act now. |

## Config markers

A config model marks a field in its type, `Annotated[T, MARKER]`; `Strict` refuses a marker given
as a default or buried inside the type (`Annotated[str, LOCKED] | None`, `list[...]`, an alias).

| Marker | What it says |
| --- | --- |
| `LOCKED` | The settings page shows the field but won't change it: it says where a credential goes, where items link with my login in the link, whose word decides a sender is who they claim, or what runs where. A section's `token_env` and `keychain_service` are locked without it. |
| `TABLES` | The field's entries are tables of their own, by name (mail's `[mail.<name>]`): `mfd init` leaves it to the example's `tables`. |
