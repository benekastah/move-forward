# Writing a plugin

Every tool the dashboard knows about is a plugin: github, git, git_stack, jira, mail, slack
and notes are all built this way, in `src/move_forward/plugins/<name>/`. Core knows no tool. It
loads the config, enables the plugins whose section is there, runs their collectors, builds
the [entity graph](graph.md) from what they publish, and renders their panels.

This page walks through what a plugin is made of, using a made-up `linear` plugin (issues
from Linear) as the example. [AGENTS.md](../AGENTS.md) has the rules every change follows;
this page explains them as they apply to a plugin.

## Requests

Every hook takes one argument, a request object, whose fields are the hook's inputs, so core
can give one a new field without changing any hook's signature. There are five kinds, all in
`move_forward.api`:

| Request | Fields | Hooks |
| --- | --- | --- |
| `ContextRequest` | `ctx` | `snapshots`, `linking_patterns` |
| `Request` | `ctx`, `graph`, `snapshot`, and `previous` for `events` | `entities`, `relate`, `panels`, `item_actions`, `after_collect`, `prune`, `events` |
| `RefreshRequest` | `ctx`, `graph`, `full`, `subjects`, and `previous` for `changed` | `collect`, `changed` |
| `RefRequest` | `ctx`, `kind`, `ids` | `address`, `fetch` |
| `InputRequest` | `ctx`, `graph`, and one of `page` (a `Page`: `query`, `path`), `form` (a `Form`: `fields`, `surface`, `back`) or `args` | pages, buttons (`actions`), `mfd <plugin>` (`cli`) |

`ctx` is the plugin's context (below), `graph` every plugin's entities (empty for `entities`,
which the graph is built from), and `snapshot` what the plugin last stored (None before its
first refresh). A field a hook doesn't get is left at its default; each field's docstring
names the hooks that get it.

`previous` is a `Previous`, what held before, for the two hooks that compare: `changed` and
`events`. Its `at` is when the plugin's last change check ran (`changed`), or when the refresh
that last stored a snapshot began, the one `events` compares with (`events`; a refresh that
failed stored nothing); `fingerprint` what its last check answered (`changed`), `snapshot` its snapshot
before this refresh (`events`), and `graph` the graph then: None at the first change check
since the dashboard started, as it's the one thing a restart can't rebuild.

## The pieces

```
src/move_forward/plugins/linear/
    config.py      # LinearConfig: the [linear] section
    models.py      # LinearSnapshot and its parts: what collect brings back
    client.py      # talking to Linear (the standard library or a CLI; no new dependencies)
    setup.py       # mfd init's questions for [linear], and its example values
    repository.py  # the plugin's tables: MIGRATIONS, and its Snapshots (save, load)
    kinds.py       # IssueAttrs: the attributes of the entities it publishes
    panels.py      # pure functions from the snapshot and the graph to panels
    plugin.py      # LinearPlugin, the Plugin subclass tying them together
    actions.py     # its buttons, if it has any
    cli.py         # `mfd linear ...`, if it has commands
```

A plugin never imports another plugin, or reads another's config, snapshot or tables.
What it needs from another goes through the graph.

## Imports

A plugin uses core only through `move_forward.api` (relatively, `from ...api import
Plugin, Context, Graph`): everything a plugin may use is there, and core's own modules may
change without notice. Its submodules group the rest: `api.html` (`page`, `e` to escape,
`shown` to drop hidden characters, `buttons`, `linkable`), `api.shell` (`run`, `git`,
`git_common_dir`, `CommandError`), `api.http` (`request`, `RequestError`), `api.localtime` (`local`, `day`, `at`), `api.ids` (`branch_id`,
`branch_parts`), `api.tags` (`handle`) and `api.keychain` (`TokenFields`, `TokenSettings`,
`token`, `read`, `write`, `delete`). Ruff refuses any other core import in a plugin, and
`tests/test_api.py` checks it too.

## Versions

Declare the version of the API the plugin is built against, `api_version = API_VERSION`
(from `move_forward.api`). A dashboard whose `API_VERSION` differs leaves the plugin out
and says why in `mfd check` and the page's footer, rather than running it against hooks
that may have changed. [api-changes.md](api-changes.md) lists what each version changed.

## Packaging it

A plugin of your own is a package that declares an entry point in the
`move_forward.plugins` group, naming its `Plugin` subclass:

```toml
# pyproject.toml
[project.entry-points."move_forward.plugins"]
linear = "move_forward_linear:LinearPlugin"
```

Install it beside the dashboard (`uv tool install move-forward --with move-forward-linear`)
and it's found when `mfd` starts. Its `[linear]` section is read as its `config_model`
(`config.section("linear")`); with no section, it's disabled. A plugin that won't import,
or whose name is taken (by a built-in, or by core: `registry.RESERVED_NAMES`, such as
`settings` or `serve`), is left out, and `mfd check` says why. Plugins load in name order.

The built-in plugins are found in `move_forward/plugins/` instead (`registry.BUILTINS`), so
a checkout run without installing still has them: each package there is one, by the one
`Plugin` subclass its `plugin.py` defines, named as the package is. Adding one changes nothing in core.

## Config

```python
class LinearConfig(TokenFields):
    """`[linear]`: issues assigned to me in Linear, read with a personal API key."""

    default_token_env: ClassVar[str] = "LINEAR_API_TOKEN"
    default_keychain_service: ClassVar[str] = "move-forward-linear"
    team: str
    """The team key, e.g. `ENG`."""
```

- Config models subclass `Strict` (or `TokenFields`, which is one), which rejects unknown
  keys: a typo is an error. A field
  whose entries are tables of their own, by name (mail's `[mail.<name>]` accounts), says so
  with `json_schema_extra={api.TABLES: True}`, and its example is one such table.
- Every field has a docstring.
- A built-in's every key appears in two places: the model and the plugin's page in
  `docs/plugins/`. A plugin from another package can't add to those pages: its model and its
  own docs are where its keys appear. `mfd init` (and `mfd init --example`) writes each
  optional key from the model, commented out, with its default and its docstring's first
  sentence as its comment; so make that sentence say all a person needs to set it: what it's
  for, its unit, the value that turns it off ("0: never", "empty: no such convention"), and
  what an empty or unset value stands for where that isn't obvious. It ends at the first `.`,
  `?` or `!` before a space, but not at `e.g.`, `i.e.` or `etc.`.
- How `mfd init` sets the plugin up is its `init`, an `Init` built in its `setup.py` (see
  github's: `INIT = Init(questions=questions, example=EXAMPLE, ...)`), and read by nothing
  else. `mfd init` asks which plugins to set up (offering by default those whose `Init` says
  `suggested=True`), then each chosen one's `questions(ask, setup)`, which returns its section
  as an `InitSection`: its `values`, and its `shown` (below). `setup` has my email, where init
  runs and the teams found so far; a plugin whose questions set the teams (github's) says so
  with `finds_teams=True`, and otherwise core asks for them by name before any plugin's
  questions. Init refuses a plugin chosen without the plugins it `requires`. `ask(question,
  default, check, complete)` and `ask.list` tab-complete with `complete`:
  `asking.choices(...)` (a list, or a function fetching it on the first tab) or
  `asking.directories`. Its `example` is an `InitSection` of the required keys with example
  values, written commented out when the plugin isn't chosen; and it says what the model
  can't: in `tables`, commented tables of example entries (`[github.teams]`), and in a
  section's `shown`, an example value for a key whose default is None or says less than an
  example, where uncommenting it is right for that section (mail's `auth_servers`: Gmail's
  servers for a Gmail host, `[]` for another, the same as unset there). Each comment goes
  above what it's about. A plugin still setting `init_template` is refused, naming these.
  `mfd init --example` writes each plugin's section as `mfd init` does when it isn't
  chosen, or, with `example_on=True` (a tool most use: github, git), on, with its `example`
  uncommented.
- Settings that say where a credential goes (a site, a host) are marked `locked()` on the
  model (`site: Annotated[str, api.locked()]`), so the settings page, reached with the
  dashboard's cookie, shows them without changing them; `token_env` and `keychain_service`
  are locked already; a section keeping its token's place under other names, or building it
  from other settings, marks those too.
- Secrets never go in the config. Build the section on `api.keychain.TokenFields`, as jira,
  slack and mail do: it gives it `token_env` and `keychain_service`, defaulting to the
  `default_token_env` and `default_keychain_service` it sets. Read the token with
  `ctx.token()`, which raises `SourceError` saying how to give one when there's none: then
  `mfd token set <plugin>` stores it and `mfd token check` finds it; its `Init`'s
  `token_help` is `mfd init`'s next step for it, saying where to get one (a section that
  keeps them under other names overrides `tokens(config)`, returning `{"": settings}`, where
  `settings` is an `api.keychain.TokenSettings`). Default `keychain_service` to one named for the plugin
  (`move-forward-linear`): the config is refused when two tokens share one. A plugin reading
  several accounts, each with its own token, names them all in `tokens(config)`, reads one
  with `ctx.token(account)`, and `mfd token set <plugin>.<account>` stores one. Anything sent with a
  token must go over HTTPS: send it with `api.http.request`, which checks, in a header or
  the body, never the URL (its errors name the URL, without its query).
- A section mapping the top-level teams to the tool's names for them (github's
  `[github.teams]`) names that field in `teams_field`, so the config refuses a team it
  doesn't have.

## Collecting and storing

```python
class LinearPlugin(Plugin[LinearConfig, LinearSnapshot]):
    name = "linear"
    title = "Linear"
    api_version = API_VERSION
    config_model = LinearConfig
    migrations: ClassVar[list[Migration]] = MIGRATIONS

    def collect(self, req: RefreshRequest[LinearConfig]) -> LinearSnapshot:
        return client.collect(req.ctx.config, req.ctx.email)

    def snapshots(self, req: ContextRequest[LinearConfig]) -> IssueRepository:
        return IssueRepository(req.ctx.db)
```

- `name` is the config section, the kv namespace and migrations' owner, the tables'
  prefix and the first part of the actions' paths: lower-case letters, digits and `_`,
  unique, and not one of `registry.RESERVED_NAMES` (core's own names, commands, top-level
  keys and paths, `core` and `act_now` among them) nor a prefix of one of core's tables or
  indexes (`team` of `team_channels`). The registry checks this when it's imported.
- Every hook gets `ctx`, the plugin's context: its section (`ctx.config`), `ctx.name`,
  `ctx.now`, `ctx.today`, `ctx.email`, `ctx.teams`, `ctx.due_horizon` (how far ahead a due
  date starts to count, as `[act_now] due_warning_days` says), its database (`ctx.db`) and key/value
  state (`ctx.kv`: `get`/`set`, `get_json(key, type)`/`set_json`, `delete`, `keys(prefix)`),
  and a logger named for it (`ctx.log`).
- Something wrong that isn't a failed refresh (a lookup the token isn't allowed, two
  clones of one repo) is a problem: `ctx.problem(key, text)` reports it, and
  `ctx.problem(key, None)` says it's gone. Core keeps it across restarts, shows it in the
  plugin's group (or the footer) until it's gone, and logs it only when it changes, so report
  it each time it's checked rather than warning of it yourself: one not reported for
  `prune_days` is forgotten.

- A plugin collects (`collects`) when it overrides `collect`, and it then overrides
  `snapshots` too: core refuses a plugin with one and not the other. It has a `refresh_seconds`
  setting in its section (with a default that suits its tool): the server refreshes it
  that often, on its own schedule.
- A plugin with a cheap way to tell that its tool has something new (a file's time, a
  request answered "not modified", another plugin's entities in the graph) sets
  `change_check_seconds` and implements `changed(req)`, which says what moved in its tool
  (a `Changed`): a `fingerprint` of its state (a digest, a mailbox's counts), the `subjects`
  that moved in the plugin's own terms (repos, PRs), `everything=True` for something moved
  that no subject names, or more than one of these, and a `reason` for the log; `Changed()`
  says nothing moved. Core keeps the answer, not the plugin: the server asks that often,
  compares each fingerprint with the last, and refreshes the plugin as soon as something
  moved since the last check (a different fingerprint, any subject, or `everything`), so name
  a subject only when it moved again. It asks again as each collect of the plugin starts,
  so what moves while it runs is compared with what it read. The last fingerprint and when
  it was taken are `req.previous.fingerprint` and `req.previous.at`, for a check that needs
  them (which repos moved, what was updated since); other plugins' entities it compares with
  `req.previous.graph`, the graph its last check was given (None at the first since the
  server started). The subjects stay pending until a collect that started after they moved
  stores, so one that fails loses none: `collect` gets them as `req.subjects`, with
  `req.full` saying whether to fetch everything anyway (its first collect since the server
  started, unless it collects only entities asked for, below, and a full collect stored
  within `refresh_seconds`; `refresh_seconds` since the last full collect that stored;
  something unnamed moved; or nothing's pending). With `changed`, `refresh_seconds` can be
  long. A `changed` that
  reads another plugin's entities from the graph names that plugin in `watches`: the
  server asks it right after each refresh of that plugin (github watches git, to see my
  PRs' branches move), whether or not it also has `change_check_seconds`.
- `collect` runs in a refresh, in parallel with the other plugins due, and may be slow: it's
  the only place a plugin talks to its tool, along with `after_collect`
  and `fetch`. A plugin that fails is recorded with its error and the page carries on with
  what it stored last. It has five minutes to finish: past that, the
  refresh records it as timed out and goes on, and leaves it out of later refreshes until
  its collect returns (a thread can't be stopped). Set a timeout on whatever it calls, too.
- `snapshots(req)` gives where the plugin keeps what `collect` returned, in its own
  tables: a `Snapshots`, usually its repository over them, whose `save(snapshot)` replaces
  what the last good collect kept and whose `load()` reads it back for every page, with the
  database held so no refresh stores between its queries (so it mustn't wait on another
  thread using `ctx.db`). Nothing is kept as JSON.
- Who I am in the tool is a `person` it publishes with `me=True`: the page's footer lists
  each plugin's, by handle.
- A plugin that needs other plugins' entities to know what to fetch sets `collects_after =
  True` (see git_stack): its `collect` runs once the others have stored their snapshots, with
  the graph they make (`req.graph`; every collect has the graph, built from what's stored). It's refreshed on its own schedule, not with the
  plugins it reads; to refresh soon after they change, it `watches` them.
- `notification_kinds` and `events(req)` make browser notifications: when the user has
  subscribed to one of the kinds (`<plugin>.<kind>`), `events` is asked after every refresh,
  of anything, with the plugin's snapshot before it and now (`req.previous.snapshot`,
  `req.snapshot`, the same when this refresh didn't fetch it, and neither None) and the graph
  before it and now (`req.previous.graph`, `req.graph`), and returns an `Event` for what became true: its
  `thing` (the plugin's events with the same one are told together; core keeps it as
  `<plugin>:<thing>`, so another plugin's events about one URL stay apart), `title`, `text`,
  and `anchor` (an item's `page.anchor(kind, id)`, to open the dashboard at it). Say it on a
  change of state, not while it holds, and nothing about what `before` has no state for.
  `events` isn't asked before the plugin's first snapshot, which has nothing to compare
  with; after a restart, "before" is what was stored when the dashboard stopped. A failure
  is logged and the refresh goes on. What's news because it's newly in a panel needs no
  `events`: the item says so (`Item.notify`, under Panels).
- `after_collect(req)` runs after every refresh, for work that needs the graph and
  writes the plugin's tables, e.g. notes finding what its ice covers.

### Tables and migrations

`MIGRATIONS` is a list of `Migration(name, run)`: `run` is SQL, or a function given the
connection for a data fix SQL can't say (it runs inside the migration's transaction and
mustn't commit). They're applied in order, and the database keeps which ran by name.
The rules:

- **Append-only.** Never edit, rename or reorder a migration that's on `main`; add a new
  one, named with the next number: `Migration("0002_projects", "...")`. Core refuses a
  list whose names don't start with the ones the database ran.
- **Prefixed.** Every table, index, view and trigger starts with the plugin's name:
  `linear_issues`. Core rolls back, and refuses, a migration that makes another, or one of
  another plugin whose name starts with yours (`git_stack_` for `git`); and refuses a plugin
  named for a prefix of core's own (`team`, of `team_channels`).
- **Fetched or kept**, and the table's comment says which. A fetched table holds what
  `collect` brought back and is replaced wholesale by its `Snapshots.save`. A kept table holds what
  only the dashboard knows (notes, pings sent, projects seen); it survives refreshes, and
  `prune` clears what's finished.
- **A cache of lookups** (a login's email, found once and kept) is a kept table of a key, a
  value, a `source` and a `looked_up_at`, which `api.LookupCache(db, table, key, value)`
  reads and writes: a value set by hand (`BY_HAND`) wins over a lookup's (`LOOKED_UP`),
  as does one the old `mfd import` read (`IMPORTED`), and `forget_misses_before(cutoff)`, from
  `prune`, lets lookups that found nothing be tried again. Its docstring has the table.
- **Order-independent.** A plugin can be enabled before or after any other, so its
  migrations touch only its own tables.

## Publishing entities

What other plugins can know of yours is the entities it publishes. Declare each kind with
a model of its attributes, and build the attributes from that model:

```python
ISSUE = "linear.issue"


class IssueAttrs(KindModel):
    """A `linear.issue`: one of my Linear issues, by its identifier, e.g. `ENG-12`."""

    state: str
    estimate: int | None = None


class LinearPlugin(...):
    kinds: ClassVar[dict[str, KindSpec]] = {
        ISSUE: KindSpec(IssueAttrs, linking=True, fetchable=True, resolvable=True)
    }

    def entities(self, req: Request[LinearConfig, LinearSnapshot]) -> list[Entity]:
        snapshot = req.snapshot
        return [
            Entity(
                ref=Ref(kind=ISSUE, id=issue.identifier),
                title=f"{issue.identifier} {issue.title}",
                url=issue.url,
                state=issue.state,
                attrs=attrs(IssueAttrs(state=issue.state, estimate=issue.estimate)),
            )
            for issue in (snapshot.issues if snapshot else [])
        ]
```

- A kind's attributes model is a `KindModel`, which rejects unknown keys: a misspelt
  attribute fails core's check (and is logged) rather than quietly publishing nothing.
  Consumers' views stay plain `Model`s.
- A plugin's kinds are named for it, `<plugin>.<name>` (`linear.issue`), or for a family
  (below); the registry refuses any other name. It publishes only kinds it declares, each
  ref once. Core checks every entity against its kind's model and leaves out (and logs) one
  that doesn't fit.
- Document the kind and its attributes in [entities.md](entities.md);
  `tests/test_entities_doc.py` fails until you do. [plugins.md](plugins.md) draws who
  publishes and reads what.
- **Families.** A kind that more than one tool could publish (a pull request, a ticket, a
  person) is a family core models in `api.kinds`: name your kind for it (`TICKET`) and
  extend its model (`class LinearIssue(Ticket)`), setting `handle` to how your tool shows
  it, and give each entity an id by the family's rule (`api.kinds.FAMILIES[...].id_rule`;
  [entities.md](entities.md#families) lists them), so it's unique across tools. Its refs
  name the family (`Ref(kind=TICKET, id=...)`), and other plugins read it through the
  family's model, whoever publishes it.
- **Your own families.** For something several tools share that core doesn't model (a CRM
  deal, say), declare the family in `families`: a `Family` with its name (lower-case, no
  dot), label, model (with a `handle`, as every family's has), id rule (`api.kinds.is_url`,
  say) and, if it's fetched on demand, its fetched model. A plugin publishes kinds only of
  core's families and those it declares itself, so every plugin publishing one declares it:
  put it in a package they import, so they declare the same one. A plugin declaring a family
  differently from core or another plugin is left out, and `mfd check` says so. The test
  harness declares your plugin's families while it runs it, and those of other plugins
  you read or seed when you pass them (`Harness(..., families=...)`). Task ids the family's consumers
  share (as `REVIEW` is for PRs) can live in that package too.
- **People.** A tool that knows who something is about publishes them as `person`s, with an
  `email` if it has one, and points the entity at them. Core then joins them with the same
  person in other tools (`graph.same_person`), and ranks and badges their items by contact type,
  which a tool that knows it (a CRM) sets in `contact_type`. Don't do either yourself.
- **Linking kinds.** A kind that stands for a unit of work (a ticket, a work entry) is
  declared `linking` (`KindSpec(..., linking=True)`), so the graph relates what's about it.
  Give the pattern of its ids in `linking_patterns` (from what the plugin has seen, if need
  be), as jira does, and other plugins' entities point at it from the text they mention; see
  [graph.md](graph.md#related-entities). A publisher puts text that might name a unit of
  work in `Entity.mentions` (github does with a PR's branch, title and body), never refs to
  another plugin's kind. Where someone else wrote that text, it sets `mentions_hide=False`,
  so what they name can't hide the entity from me (see
  [hidden entities](graph.md#hidden-entities)).
- **Hiding kinds**, declared `KindSpec(..., hides=True)`, hide what their entities point at
  (`notes.iced`).
- **Lifecycle.** Each entity says whether its work is over: `lifecycle="done"` for a merged
  PR or a resolved issue (its `state` keeps the tool's own word). Core clears priorities by
  it. A kind whose entities are published only while open (notes' todos) is
  `gone_means_done`; one whose entities `fetch` can look up after they've left the
  snapshot is `fetchable`; one `mfd priority <id>` should look in without `--kind` is
  `resolvable`.
- **Tags.** A `#tag` in an entity's title or `mentions` (out of Markdown code and HTML
  comments) puts the tag on it, and text there naming one points it at the tag (see the
  README's Tags). Put the tool's labels in `Entity.tags`, as written ("Garbage Day"): core
  makes them tags. Put only labels no outsider can set (people with a say in the tool, or the
  tool itself), even on a kind whose text anyone can write, and decide whether the tool's own
  (Gmail's `\Inbox`) are tags too: mail's are. A tag only labels give is named only with its
  `#`, as a label is often a common word. I can take a label's tag off, until your plugin next
  refreshes. A kind whose text anyone can write (mail's subjects) is
  `KindSpec(..., tags_from_text=False)`. Read the tags on or named by an entity with
  `graph.related(ref, TAG)` (`api.kinds.TAG`).
- `address(req)` (`req.kind`, `req.ids`) says where entities that may not be in the graph
  are and what they're called, by id: an `Address(url, handle)` (a ticket's URL from its key,
  its key from its URL), leaving out what it can't say. `fetch(req)` fetches entities by id
  from the tool now, for rare work like the daily prune; what a fetched entity must carry is
  in [entities.md](entities.md#fetched-on-demand). Core asks each only about the plugin's own
  `kinds`, so they needn't check `req.kind` is theirs; `address` gives the id as the URL by
  default where its family's ids are URLs (a pull request's), so a plugin overrides it only
  to make a URL of something else (a ticket's key), or to give a handle.
- A branch's id is `api.ids.branch_id(repo_dir, name)`, read with `branch_parts(id)`.
- **Relations.** What only your plugin knows links one of its entities to another's (a PR
  to its local branch) goes in `relate(req)`, as `(mine, theirs)` pairs; see
  [graph.md](graph.md#relations).

## Reading other plugins' entities

Read another plugin's entities through a small model of your own, a view, listing only the
fields you use (`graph.view_related(ref, PULL_REQUEST, _PrView)`), never `entity.attrs`: if
the publisher renames an attribute, the view raises and your tests fail, instead of the page
quietly showing less. [graph.md](graph.md#through-a-view) shows one, and
[Reading it](graph.md#reading-it) the rest: `view`, `view_one`, `view_related`, `related`,
`named`, `hidden` and `fetch`.

## Panels

```python
def panels(self, req: Request[LinearConfig, LinearSnapshot]) -> list[Panel]:
    snapshot, graph = req.snapshot, req.graph
    return [
        Panel(
            key="assigned",
            priority=Band.MINE,
            title="Linear issues",
            description="Issues assigned to me, oldest first.",
            items=[...],
        )
    ]
```

- Panel builders are pure: no I/O, no clock (use `ctx.now`), no database reads beyond what
  the plugin passes in. Anything slow belongs in `collect`.
- The page keys a panel `<plugin>.<key>` (`linear.assigned`) and groups a plugin's panels
  together. `priority` is the `Band` saying what kind of panel it is, which sets the
  default order, lowest first: `PLAN` (my todos), `ASKED` (others wait on me),
  `MINE` (my own work needs me), `WAITING` (I wait on others), `HOUSEKEEPING`, `NEXT` (what
  to pick up) and `PARKED`. A plugin's panels in one band go in the order `panels` lists
  them, and plugins' groups by their most urgent band, then by name. The person can drag
  panels and groups about, and that wins.
- An item's `rank` orders it within its panel only; Act now orders by score.
- Each `Item` names its `subjects`, the entities it's about, main one first. Other plugins
  add their buttons by them, and core hides items whose subject is on ice.
- A badge is a string, or `Badge(text, tone="warn")` for one that needs attention (`good`
  for one going well). A link to what's over sets `done`, and is struck through. An item
  that's not for now (a draft) is `deferred`: it sorts last, and Act now leaves it out
  unless it says it's a candidate (`act_now=True`, below).
- An item or part whose work is done but kept a while to undo (a todo ticked off today) is
  `done`: it's dimmed and last, isn't counted, isn't in Act now, and focus mode skips it; nor
  does it count towards what brings back a snoozed item. Mark an item whose parts are all done
  `done` too. A part that's `done` gets nothing from core or other plugins (no Tag, no
  importance): only its own buttons act on it. An item that's `done` gets what any item does.
- An item's `parts` (a work entry's todos) are listed beneath it when there are several, on the
  dashboard and in Act now, and shown one at a time in focus mode, each with its own buttons.
  A part listed beneath its item doesn't repeat a button the item has. Their titles count towards what brings back a snoozed item, so `details` needn't repeat them.
- An item whose arrival is news (a branch joining Cleanup) sets `notify=Notify(kind, text)`,
  `kind` one of the plugin's `notification_kinds`: after each refresh core compares the
  plugin's panels with the last refresh's, and raises it for each item newly there, by its
  kind and `thing` (its main subject's id unless it gives one; an item without subjects must,
  since its title may change between refreshes). The first refresh only keeps
  what's there, a restart doesn't tell it all again, and an item that leaves and comes back
  is told again. The notification's title is the item's, and it opens the page at the item.

### Act now

Core's Act now ranks what every plugin nominates. Mark a panel `act_now=True` and give it
default `signals`, and its items are nominated:

```python
Panel(..., act_now=True, signals=Signals(verb="Triage", task="triage", effort="minutes"))
```

An item can carry its own `signals` (`effort`, `closes`, `waiting` and `waiting_since`,
`importance` from 0 to 2, `due`, `blocked`, `verb`), and say itself whether it's a
candidate, whatever its panel: `Item(act_now=True)` nominates it (a ticket due soon in a
panel that isn't for now, the feedback on a draft, deferred as it is), and
`Item(act_now=False)` keeps it out. A candidate with no signals, its own or its panel's,
isn't one. Map your tool's own idea of
importance (a priority, an escalation) onto `importance`, in the plugin. An item whose main
subject is a unit of work (a linking kind, like a ticket) joins the best item about
something in that unit, so give such items a `verb` that reads well after another's.
A plugin whose items are candidates by due date passes `ctx.due_horizon` to `due_pressure`,
so it agrees with `[act_now] due_warning_days`.
Set `Item.fingerprint` to what would make a snoozed item worth another look.
An item with a `task` also has Snooze in its own panel; what's snoozed
waits there, last and dimmed, with Unsnooze, and Act now leaves it out. Give a task to any item it makes
sense to set aside.

Every nominated item needs a `task`: a stable id for what doing it is (`triage`,
`transition`), lower-case letters, digits and `_`. Act now keeps a snooze or pin
by the item's main subject and its task, so `verb`, the words shown, can change (a status
name, a count) without losing them. Core leaves out, and logs, a nomination without one.

## Buttons, pages and commands

- **Actions.** `actions()` maps names to handlers `(req: InputRequest) -> str |
  ActionResult`; a button posts to `/actions/<plugin>/<name>`, which is its `Action.name`.
  Core leaves out (and logs) a button whose name no enabled plugin's `actions()` has, so a
  typo can't post to a 404. `req.form` is the form posted: `req.form.fields` its
  fields, `req.form.surface` where the button was (`dashboard` or `focus`). A button with an
  `input` has a text field, posted as `input`; `suggestions` lists what it offers to pick
  from, and `choices` adds a drop-down beside it (label to value), posted as `choice`. Check every field: they
  come from a form, so treat them as untrusted (a repo must be configured, a worktree must
  be the repo's, an id must exist). Anything that deletes or rewrites sets `confirm`, and
  nothing force-pushes, resets or stashes. Read a required field with
  `req.field(name)`, which says "missing field" when the form lacks it. Raise
  `ActionError` with a message the page can show; anything else a handler raises is shown
  as "<plugin>/<name> failed" and logged.
- **Adding to other plugins' items.** `item_actions(req)` is called once per page, with
  `req.graph`, and returns a function (`ItemAdditionsFor`) that core calls for each item
  with an `ItemRequest`: it returns the `ItemAdditions` (buttons, badges, details, links)
  for `each.item`, by its subjects; `each.owner` is the plugin whose item it is and
  `each.surface` where it's shown (`dashboard`, `focus`, `act_now`). Read your tables, or
  make a pass over the graph, in `item_actions` itself, once, not in the function.
- **After an action** the server refreshes the acting plugin. An action that changed
  something another plugin publishes (reacting to a PR, say) lists it in
  `ActionResult(notice, touched=[ref])`, and that entity is fetched again (below).
- **Fetching a few entities again.** An action's `touched` refs, a tab coming back to
  focus and `mfd refresh --subject` refresh the publisher, its `collect` given the entities'
  ids in `req.subjects` (with what its change checks said moved) and `req.full` False, unless
  a full collect is due, even at its first collect since the server started. When the
  publisher is refreshed by name or schedule in the same round (its Refresh button, its own
  action), it's collected as it would have been, given the ids too. It's the plugin's refresh
  like any other: recorded, told, and its scheduled one waits its `refresh_seconds` again. A `collect` that can fetch just those does
  (github fetches the PRs in one request); one that can't, or gets a subject it doesn't know,
  fetches everything. One that fails is recorded as failed, as any refresh.
- **Focus mode keeps the item** a button was pressed on, should the button take it out of
  the category (a todo ticked off): core remembers it and shows it as the page now has it (the
  todo ticked, to untick), found by its main subject, so give what an action leaves on the
  page (a ticked todo) the same subject it had.
- **Pages.** `pages()` maps keys to handlers `(req: InputRequest) -> str | Response | None`
  (with `req.page.query` and `req.page.path`); core serves each at `/<plugin>/<key>`, and `""` at
  `/<plugin>`, so two plugins' `ping` pages never clash. A handler returns HTML (use
  `api.html.page` and `api.html.e` to escape everything), or a `Response`, e.g.
  `Response.redirect(url)`. Every page needs the
  dashboard's token, as every request does.
- **Commands.** Implement `cli(parser)` for `mfd linear ...`, returning a handler `(req:
  InputRequest) -> int` that gets the parsed `req.args`. The first line of its docstring is
  the command's help in `mfd --help`; core refuses a `cli` without one.

A hook that raises takes down only what it was building: a failing `entities` leaves the
plugin's entities out of the graph, a failing `panels` shows one "(failed)" panel in their
place, a failing page answers 500, and each is logged (once every ten minutes for the same
plugin and hook). The rest of the page, and the other plugins' refreshes, go on.

## Upkeep

`prune(req)` runs once a day: clear kept data about work finished more than
`ctx.prune_days` ago, and return what was done.

## Tests

`move_forward.testing.Harness` runs a plugin as core runs it: enabled alone, over an
in-memory database, with core's graph, page, actions and pages. It refuses a plugin core's
checks would leave out. It doesn't import pytest; wrap it in a fixture of your own.

```python
def test_triage_lists_my_open_issues() -> None:
    linear = Harness(LinearPlugin(), {"team": "ENG"})  # the [linear] section
    linear.store(LinearSnapshot(issues=[issue("ENG-1")]))  # as a refresh keeps it
    linear.with_entities([pull_request])  # another plugin's, as it would publish them
    assert [i.title for i in linear.act_now()] == ["Triage: ENG-1 Fix it"]
    assert linear.run_action("close", {"key": "ENG-1"}) == "closed ENG-1"
```

It has `entities()` (what the graph holds of the plugin's), `panels()`, `dashboard()`,
`act_now()`, `render_item(item)`, `run_action(name, fields)`, `page(key, query)`,
`events()` (of the last `store`), `prune()`, and `tick(seconds)` to move its clock. For a
plugin with a change check, `changed()` asks it as the server does, and is its answer when
something new moved (so the server would refresh it) or None; `collect()` collects and stores
as a refresh does, given what moved since the last collect that stored.
`tests/plugins/test_jira_panels.py` uses it.

- **Panel tests** build the page as core does and assert on panel contents.
- **Contract tests** assert on the entities the plugin publishes.
- Tests never touch the network: monkeypatch the client. Tests touching git make real
  repos in `tmp_path`.
- Make sure each test would fail without the change it covers.

A built-in's tests go in `tests/plugins/`, named after it (`test_jira_*.py`); those of
built-ins working together use `conftest.dashboard(...)` and `conftest.stored(...)`.

## Docs to update in the same commit

For a built-in, in this repository; a plugin from another package keeps the same in its own
docs, and its `Init`'s `example`, `tables` and its sections' `shown`.

- The plugin's page, `docs/plugins/<name>.md` (copy one of the others): its config and
  setup, what it publishes, its panels, what it nominates for Act now, its buttons, pages
  and commands. Add a row for it to the README's plugins table.
- [entities.md](entities.md) for its kinds, [plugins.md](plugins.md) for what it publishes
  and reads.
- Its config keys' docstrings, which `mfd init` writes from, and what its model can't say:
  its `Init`'s `example`, `tables` and its sections' `shown`.
