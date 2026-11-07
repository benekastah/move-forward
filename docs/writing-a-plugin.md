# Writing a plugin

Every tool the dashboard knows about is a plugin: github, git, git_stack, jira, slack and
notes are all built this way, in `src/move_forward/plugins/<name>/`. Core knows no tool. It
loads the config, enables the plugins whose section is there, runs their collectors, builds
the [entity graph](graph.md) from what they publish, and renders their panels.

This page walks through what a plugin is made of, using a made-up `linear` plugin (issues
from Linear) as the example. [AGENTS.md](../AGENTS.md) has the rules every change follows;
this page explains them as they apply to a plugin.

Every hook takes one argument, a request object (`PanelsRequest`, `EntitiesRequest`, …,
all in `move_forward.api`), whose fields are the hook's inputs: `req.ctx`, `req.snapshot`,
`req.graph` and so on. Core can give a hook a new input without changing its signature.

## The pieces

```
src/move_forward/plugins/linear/
    config.py      # LinearConfig: the [linear] section
    models.py      # LinearSnapshot and its parts: what collect brings back
    client.py      # talking to Linear (the standard library or a CLI; no new dependencies)
    setup.py       # mfd init's questions for [linear], and its commented defaults
    repository.py  # the plugin's tables: MIGRATIONS, and store/load queries
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
`buttons`, `linkable`), `api.shell` (`run`, `git`, `CommandError`), `api.http`
(`opener`, `require_https`), `api.localtime` (`local`, `day`), `api.ids` (`branch_id`, `branch_parts`) and `api.keychain`
(`token`, `read`, `write`, `delete`). Ruff refuses any other core import in a plugin, and
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

The built-in plugins are listed in `registry.BUILTINS` instead, so a checkout run without
installing still has them; adding one there is a core change, landed in its own commit
ahead of the plugin's own.

## Config

```python
class LinearConfig(Strict):
    """`[linear]`: issues assigned to me in Linear."""

    team: str
    """The team key, e.g. `ENG`."""
    token_env: str = "LINEAR_API_TOKEN"
    """The environment variable holding a personal API key."""
```

- Config models subclass `Strict`, which rejects unknown keys: a typo is an error.
- Every field has a docstring.
- Every key appears in four places: the model, the plugin's page in `docs/plugins/`,
  `mfd init`'s rendered config as a commented default (the plugin's `init_template`),
  and `mfd init --example` (`cli.py`).
- `mfd init` asks which plugins to set up (offering those whose `init_suggested()` is
  true), then each chosen one's `init_questions(ask, setup)`, which returns its section's
  values. `setup` has my email, where init runs and the teams found so far; a plugin that
  sets the teams (github does) says so with `init_finds_teams`, and otherwise core asks
  for them by name before any plugin's questions. Init refuses a plugin chosen without the
  plugins it `requires`. `ask(question, default, check, complete)` and `ask.list`
  tab-complete with `complete`: `asking.choices(...)` (a list, or a function fetching it
  on the first tab) or `asking.directories`. Keep the questions and guesses in a
  `setup.py` (see github's), with `init_template` (the optional keys, commented) and `init_example` (the
  required ones, commented, written when the plugin isn't chosen).
- Secrets never go in the config. Give the section `token_env` and `keychain_service`, as
  jira and slack do, and read the token with `keychain.token(config, email)`: then
  `mfd token set <plugin>` stores it and `mfd token check` finds it (a section that keeps
  them under other names overrides `token_settings(config)`). Anything sent with a
  token must go over HTTPS (`api.http`).
- A section mapping the top-level teams to the tool's names for them (github's
  `[github.teams]`) names that field in `teams_field`, so the config refuses a team it
  doesn't have.
- A tool whose sign-in sends the browser back to the dashboard (OAuth) is given
  `ctx.dashboard_url` (`http://localhost:8765`, or `https://` when it serves HTTPS) to
  build its redirect URL from, and a page of the plugin's to land on.

## Collecting and storing

```python
class LinearPlugin(Plugin[LinearConfig, LinearSnapshot]):
    name = "linear"
    title = "Linear"
    api_version = API_VERSION
    config_model = LinearConfig
    snapshot_model = LinearSnapshot
    migrations: ClassVar[list[Migration]] = MIGRATIONS

    def collect(self, req: CollectRequest[LinearConfig]) -> LinearSnapshot:
        return client.collect(req.ctx.config, req.ctx.email)

    def store(self, req: StoreRequest[LinearConfig, LinearSnapshot]) -> None:
        IssueRepository(req.ctx.db).replace(req.snapshot)

    def load(self, req: LoadRequest[LinearConfig]) -> LinearSnapshot | None:
        return IssueRepository(req.ctx.db).load()
```

- `name` is the config section, the kv namespace and migrations' owner, the tables'
  prefix and the first part of the actions' paths: lower-case letters, digits and `_`,
  unique, and not `core` or `act_now`. The registry checks this when it's imported.
- Every hook gets `ctx`, the plugin's context: its section (`ctx.config`), `ctx.name`,
  `ctx.now`, `ctx.today`, `ctx.email`, `ctx.teams`, its database (`ctx.db`) and key/value
  state (`ctx.kv`: `get`/`set`, `get_json(key, type)`/`set_json`, `delete`, `keys(prefix)`),
  a logger named for it (`ctx.log`), and `ctx.data_dir`, a private directory of its own for
  files, made on first use beside the database.

- A plugin that collects (`collects`: it has a `snapshot_model`) has a `refresh_seconds`
  setting in its section (with a default that suits its tool): the server refreshes it
  that often, on its own schedule. One scheduled some other way overrides
  `refresh_seconds(ctx)`.
- A plugin with a cheap way to tell that its tool has something new (a file's time, a
  request answered "not modified", another plugin's entities in the graph) sets
  `change_check_seconds` and implements `changed(req)`: the server asks that often, and
  refreshes the plugin as soon as it says True, or `Changed(True, reason)`, whose reason
  goes into the log line. It keeps what it compares against in `ctx.kv`. After each refresh of the plugin
  the server asks once more and ignores the answer, so what the refresh did itself isn't
  taken for a change. With `changed`, `refresh_seconds` can be long. A `changed` that
  reads another plugin's entities from the graph names that plugin in `watches`: the
  server asks it right after each refresh of that plugin (github watches git, to see my
  PRs' branches move), whether or not it also has `change_check_seconds`. A plugin that
  collects after the graph is refreshed with the plugins it `requires`, unless it watches
  them too: then only when its `changed` says so, with its own `refresh_seconds` as the
  backstop.
- `collect` runs in a refresh, in parallel with the other plugins due, and may be slow: it's
  the only place a plugin talks to its tool, along with `after_collect`, `collect_after`
  and `fetch`. A plugin that fails is recorded with its error and the page carries on with
  what it stored last. It has `collect_timeout_seconds` (300) to finish: past that, the
  refresh records it as timed out and goes on, and leaves it out of later refreshes until
  its collect returns (a thread can't be stopped). Set a timeout on whatever it calls, too.
  With more to fetch than fits, return `Collected(snapshot, more=True)` and keep your
  place in `ctx.kv`: core stores that piece and calls `collect` again in the same refresh,
  each piece with the timeout to itself.
- `store` keeps what `collect` returned in the plugin's own tables; `load` reads it back
  for every page. Nothing is kept as JSON.
- `identity(req)` says who I am in the tool, `Identity(tool=self.title, login=...)`, for
  the page's footer, which lists each plugin's.
- A plugin that needs other plugins' entities to know what to fetch sets
  `collects_after_graph = True` and implements `collect_after(req)` instead (see
  git_stack).
- `notification_kinds` and `events(req)` make browser notifications: when the user has
  subscribed to one of the kinds (`<plugin>.<kind>`), `events` gets the last good
  snapshot (`req.before`), the one just collected (`req.after`) and the graph from before
  the refresh, and returns an `Event` for what changed: its
  `thing` (the plugin's events with the same one are told together; core keeps it as
  `<plugin>:<thing>`, so another plugin's events about one URL stay apart), `title`, `text`, and `anchor` (an
  item's `page.anchor(kind, id)`, to open the dashboard at it). Say it on a change of state,
  not while it holds, and nothing about what `before` has no state for. A failure is
  logged and the refresh goes on.
- `graph_events(req)` makes events from what the graph says, e.g. a ticket's PRs all
  merged: compare what holds now (`req.graph`, `req.snapshot`) with what held before the
  refresh (`req.previous_graph`, `req.previous_snapshot`) and say what became true. Before
  the plugin's first snapshot `previous_snapshot` is None: say nothing then. After a
  restart, "before" is what was stored when the dashboard stopped.
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
  `linear_issues`. Core rolls back, and refuses, a migration that makes another.
- **Fetched or kept**, and the table's comment says which. A fetched table holds what
  `collect` brought back and is replaced wholesale by `store`. A kept table holds what
  only the dashboard knows (notes, pings sent, projects seen); it survives refreshes, and
  `prune` clears what's finished.
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
        ISSUE: KindSpec(IssueAttrs, linking=None, fetchable=True, resolvable=True)
    }

    def entities(self, req: EntitiesRequest[LinearConfig, LinearSnapshot]) -> list[Entity]:
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
  it, and give each entity an id by the family's rule (`api.kinds.FAMILIES[...].id_rule`:
  a URL for a ticket or a PR, `<host>/<login>` for a person), so it's unique across tools. Its refs name the family (`Ref(kind=TICKET, id=...)`), and other plugins read it
  through the family's model, whoever publishes it.
- **Linking kinds.** A kind that stands for a unit of work (a ticket, a work entry) is
  declared `linking`, so the graph relates what's about it. Give it the pattern of its ids
  (`KindSpec(..., linking=r"...")`, or None and narrow it from what the plugin has seen)
  and other plugins' entities point at it from the text they mention:

  ```python
  def linking_patterns(self, req: LinkingRequest[LinearConfig]) -> dict[str, str | None]:
      return {ISSUE: rf"\b{re.escape(req.ctx.config.team)}-\d+\b"}
  ```

  A publisher puts text that might name a unit of work in `Entity.mentions` (github does
  with a PR's branch, title and body), never refs to another plugin's kind.
- **Hiding kinds**, declared `KindSpec(..., hides=True)`, hide what their entities point at
  (`notes.iced`).
- **Lifecycle.** Each entity says whether its work is over: `lifecycle="done"` for a merged
  PR or a resolved issue (its `state` keeps the tool's own word). Core clears priorities by
  it. A kind whose entities are published only while open (notes' todos) is
  `gone_means_done`; one whose entities `fetch` can look up after they've left the
  snapshot is `fetchable`; one `mfd priority <id>` should look in without `--kind` is
  `resolvable`.
- `url_for(req)` (`req.kind`, `req.id`) makes a link for an entity that isn't in the
  graph, `handle_for(req)` what to call it (a ticket's key from its URL), and `fetch(req)` (`req.kind`, `req.ids`) fetches entities by id from the tool now, for rare work like the
  daily prune.
  Each must say its `lifecycle`, and its attributes fit `KindSpec.fetched_attrs` (a
  family's are core's: `PullRequestFetched`, `TicketFetched`), else any of the kind's own.
- A branch's id is `api.ids.branch_id(repo_dir, name)`, read with `branch_parts(id)`.
- **Relations.** What only your plugin knows links one of its entities to another's (a PR
  to its local branch) goes in `relate(req)`, as `(mine, theirs)` pairs; see
  [graph.md](graph.md#relations).

## Reading other plugins' entities

Read another plugin's entities through a small model of your own, a view, listing only the
fields you use:

```python
class _PrView(Model):
    """What linear reads of a `pull_request`."""

    id: str
    state: str | None = None
    mine: bool


for pr in graph.view_related(Ref(kind=ISSUE, id=key), PULL_REQUEST, _PrView):
    ...
```

If the publisher renames an attribute, the view raises and your tests fail, instead of
the page quietly showing less. See [graph.md](graph.md#reading-it) for `view`, `view_one`,
`view_related`, `related`, `named`, `hidden` and `fetch`.

## Panels

```python
def panels(self, req: PanelsRequest[LinearConfig, LinearSnapshot]) -> list[Panel]:
    snapshot, graph = req.snapshot, req.graph
    return [
        Panel(
            key="assigned",
            priority=Band.TRANSITIONS + 5,
            title="Linear issues",
            description="Issues assigned to me, oldest first.",
            items=[...],
        )
    ]
```

- Panel builders are pure: no I/O, no clock (use `ctx.now`), no database reads beyond what
  the plugin passes in. Anything slow belongs in `collect`.
- The page keys a panel `<plugin>.<key>` (`linear.assigned`) and groups a plugin's panels
  together. `priority` sets the default order, lowest first, in the skill's step order:
  pick the `Band` it belongs in (`Band.REVIEWS`, `Band.CI`, `Band.NEXT`, ...), adding to
  one to fall between two. The person can drag panels and groups about, and that wins.
- An item's `rank` orders it within its panel only; Act now orders by score.
- Each `Item` names its `subjects`, the entities it's about, main one first. Other plugins
  add their buttons by them, and core hides items whose subject is on ice.
- A badge is a string, or `Badge(text, tone="warn")` for one that needs attention (`good`
  for one going well). A link to what's over sets `done`, and is struck through. An item
  that's not for now (a draft) is `deferred`: it sorts last, and Act now leaves it out
  unless the plugin's `act_now` nominates it.
- An item's `parts` (a work entry's todos) are listed beneath it when there are several, on the
  dashboard and in Act now, and shown one at a time in focus mode, each with its own buttons.
  A part listed beneath its item doesn't repeat a button the item has. Their titles count towards what brings back a snoozed item, so `details` needn't repeat them.

### Act now

Core's Act now ranks what every plugin nominates. Mark a panel `act_now=True` and give it
default `signals`, and its items are nominated:

```python
Panel(..., act_now=True, signals=Signals(verb="Triage", task="triage", effort="minutes"))
```

An item can carry its own `signals` (`effort`, `closes`, `waiting` and `waiting_since`,
`importance` from 0 to 2, `due`, `blocked`, `verb`), and a plugin can override
`act_now(req)` (`req.panels` are its own) to nominate precisely. Map your tool's own idea of
importance (a priority, an escalation) onto `importance`, in the plugin. An item whose main
subject is a unit of work (a linking kind, like a ticket) joins the best item about
something in that unit, so give such items a `verb` that reads well after another's.
A plugin that nominates by due date passes `req.due_horizon` to `due_pressure`, so it agrees
with `[act_now] due_warning_days`.
Set `Item.fingerprint` to what would make a snoozed or dismissed item worth another look.
Set `snoozable=True` on such a panel to give its items a Snooze there too; what's snoozed or
dismissed then stays off the panel as well.

Every nominated item needs a `task`: a stable id for what doing it is (`triage`,
`transition`), lower-case letters, digits and `_`. Act now keeps a snooze, dismissal or pin
by the item's main subject and its task, so `verb`, the words shown, can change (a status
name, a count) without losing them. Core leaves out, and logs, a nomination without one.

## Buttons, pages and commands

- **Actions.** `actions()` maps names to handlers `(req: ActionRequest) -> str |
  ActionResult`; a button posts to `/actions/<plugin>/<name>`, which is its `Action.name`.
  Core leaves out (and logs) a button whose name no enabled plugin's `actions()` has, so a
  typo can't post to a 404. An action that keeps its item on the focus page returns
  `ActionResult(notice, req.ctx.kept(key))`, and `kept_item` gets `key` back.
  `req.fields` are its form
  fields, `req.surface` where the button was (`dashboard` or `focus`). A button with an
  `input` has a text field, posted as `input`; `suggestions` lists what it offers to pick
  from, and `choices` adds a drop-down beside it (label to value), posted as `choice`. Check every field: they
  come from a form, so treat them as untrusted (a repo must be configured, a worktree must
  be the repo's, an id must exist). Anything that deletes or rewrites sets `confirm`, and
  nothing force-pushes, resets or stashes. Read a required field with
  `req.field(name)`, which says "missing field" when the form lacks it. Raise
  `ActionError` with a message the page can show; anything else a handler raises is shown
  as "<plugin>/<name> failed" and logged.
- **Adding to other plugins' items.** `item_actions(req)` returns `ItemAdditions`
  (buttons, badges, details, links) for `req.item`, by its subjects; `req.owner` is the
  plugin whose item it is and `req.surface` where it's shown (`dashboard`, `focus`,
  `act_now`). A plugin that reads its tables for them does so once per page in
  `prepare_item_actions(req)`, returning the per-item function.
- **After an action** the server refreshes the acting plugin. An action that changed
  something another plugin publishes (reacting to a PR, say) lists it in
  `ActionResult(notice, touched=[ref])`, and that entity is fetched again (below).
- **Fetching a few entities again.** `refresh_subjects(req)` (`req.refs`, of kinds the
  plugin publishes; `req.snapshot`, what it last stored) fetches just those entities now
  and returns `Subjects(snapshot, updated)`, the snapshot with them folded in, which core
  keeps as a refresh's would (events too). It's what an action's `touched` refs, a tab
  coming back to focus and `mfd refresh --subject` use; returning None (the default), or
  failing or taking longer than `subjects_timeout_seconds`, refreshes the plugin whole
  instead. github fetches the PRs in one request.
- **Keeping an item on the focus page** after an action changes it: return
  `ActionResult(notice, req.ctx.kept(key))` and implement `kept_item`.
- **Pages.** `pages()` maps keys to handlers `(req: PageRequest) -> str | Response | None`
  (with `req.query` and `req.path`); core serves each at `/<plugin>/<key>`, and `""` at
  `/<plugin>`, so two plugins' `ping` pages never clash. A handler returns HTML (use
  `api.html.page` and `api.html.e` to escape everything), or a `Response`, e.g.
  `Response.redirect(url)`. `ctx.page_url(key)` is a page's full URL. A page a tool's
  sign-in (OAuth) redirects back to goes in `sign_in_pages` too, by its key: browsers don't
  send the dashboard's cookie on a redirect from another site, so it's served without one,
  and must check OAuth's single-use `state` before doing anything. Only the plugin's own
  pages can be: a key that isn't one of `pages()` is ignored.
- **Commands.** Set `cli_help` and implement `cli(parser)` for `mfd linear ...`, returning
  a handler `(req: CliRequest) -> int` that gets the parsed `req.args`.

A hook that raises takes down only what it was building: a failing `entities` leaves the
plugin's entities out of the graph, a failing `panels` shows one "(failed)" panel in their
place, a failing page answers 500, and each is logged (once every ten minutes for the same
plugin and hook). The rest of the page, and the other plugins' refreshes, go on.

## Upkeep

`prune(req)` runs once a day: clear kept data about work finished more than
`ctx.prune_days` ago, and return what was done.

## Tests

`move_forward.testing.Harness` runs a plugin as core runs it: enabled alone, over an
in-memory database, with core's graph, page, actions and pages. It doesn't import pytest;
wrap it in a fixture of your own.

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
`graph_events()` (since the last `store`), `prune()`, and `tick(seconds)` to move its clock.
`tests/plugins/test_jira_panels.py` uses it.

- **Panel tests** build the page as core does and assert on panel contents.
- **Contract tests** assert on the entities the plugin publishes.
- Tests never touch the network: monkeypatch the client. Tests touching git make real
  repos in `tmp_path`.
- Make sure each test would fail without the change it covers.

A built-in's tests go in `tests/plugins/`, named after it (`test_jira_*.py`); those of
built-ins working together use `conftest.dashboard(...)` and `conftest.stored(...)`.

## Docs to update in the same commit

- The plugin's page, `docs/plugins/<name>.md` (copy one of the others): its config and
  setup, what it publishes, its panels, what it nominates for Act now, its buttons, pages
  and commands. Add a row for it to the README's plugins table.
- [entities.md](entities.md) for its kinds, [plugins.md](plugins.md) for what it publishes
  and reads.
- `mfd init`'s rendered config and `mfd init --example` for its config keys.
