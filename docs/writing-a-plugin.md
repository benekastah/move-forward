# Writing a plugin

Every tool the dashboard knows about is a plugin: github, git, git_stack, jira, slack and
notes are all built this way, in `src/move_forward/plugins/<name>/`. Core knows no tool. It
loads the config, enables the plugins whose section is there, runs their collectors, builds
the [entity graph](graph.md) from what they publish, and renders their panels.

This page walks through what a plugin is made of, using a made-up `linear` plugin (issues
from Linear) as the example. [AGENTS.md](../AGENTS.md) has the rules every change follows;
this page explains them as they apply to a plugin.

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

## Registering it

1. Add an instance to `PLUGINS` in `registry.py`.
2. Add a field for its section to `Config` in `config.py`, typed `LinearConfig | None`
   (None, the section missing, means disabled).

Both are core changes: land them in their own commit, ahead of the plugin's own.

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
  `mfd token set <plugin>` stores it and `mfd token check` finds it. Anything sent with a token must go over HTTPS (`move_forward.https`).
- A tool whose sign-in sends the browser back to the dashboard (OAuth) is given
  `ctx.dashboard_url` (`http://localhost:8765`, or `https://` when it serves HTTPS) to
  build its redirect URL from, and a page of the plugin's to land on.

## Collecting and storing

```python
class LinearPlugin(Plugin[LinearConfig, LinearSnapshot]):
    name = "linear"
    title = "Linear"
    config_model = LinearConfig
    snapshot_model = LinearSnapshot
    migrations: ClassVar[list[str]] = MIGRATIONS

    def collect(self, ctx: Context[LinearConfig]) -> LinearSnapshot:
        return client.collect(ctx.config, ctx.email)

    def store(self, ctx: Context[LinearConfig], snapshot: LinearSnapshot) -> None:
        IssueRepository(ctx.db).replace(snapshot)

    def load(self, ctx: Context[LinearConfig]) -> LinearSnapshot | None:
        return IssueRepository(ctx.db).load()
```

- A plugin that collects has a `refresh_seconds` setting in its section (with a default
  that suits its tool): the server refreshes it that often, on its own schedule.
- A plugin with a cheap way to tell that its tool has something new (a file's time, a
  request answered "not modified", another plugin's entities in the graph) sets
  `change_check_seconds` and implements `changed(ctx, graph)`: the server asks that often, and refreshes the plugin as soon as it says
  True. It keeps what it compares against in `ctx.kv`. After each refresh of the plugin
  the server asks once more and ignores the answer, so what the refresh did itself isn't
  taken for a change. With `changed`, `refresh_seconds` can be long. A `changed` that
  reads another plugin's entities from the graph names that plugin in `watches`: the
  server asks it right after each refresh of that plugin (github watches git, to see my
  PRs' branches move), whether or not it also has `change_check_seconds`.
- `collect` runs in a refresh, in parallel with the other plugins due, and may be slow: it's
  the only place a plugin talks to its tool, along with `after_collect`, `collect_after`
  and `fetch`. A plugin that fails is recorded with its error and the page carries on with
  what it stored last.
- `store` keeps what `collect` returned in the plugin's own tables; `load` reads it back
  for every page. Nothing is kept as JSON.
- A plugin that needs other plugins' entities to know what to fetch sets
  `collects_after_graph = True` and implements `collect_after(ctx, graph)` instead (see
  git_stack).
- `after_collect(ctx, graph)` runs after every refresh, for work that needs the graph and
  writes the plugin's tables, e.g. notes finding what its ice covers.

### Tables and migrations

`MIGRATIONS` is a list of SQL scripts, applied in order and counted in
`schema_versions`. The rules:

- **Append-only.** Never edit a migration that's on `main`; add a new one.
- **Prefixed.** Every table starts with the plugin's name: `linear_issues`.
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
class IssueAttrs(Model):
    """An `issue`: one of my Linear issues, by its identifier, e.g. `ENG-12`."""

    state: str
    estimate: int | None = None


class LinearPlugin(...):
    kinds: ClassVar[dict[str, type[BaseModel]]] = {ISSUE: IssueAttrs}

    def entities(self, ctx, snapshot) -> list[Entity]:
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

- A plugin publishes only kinds it declares, each ref once. Core checks every entity
  against its kind's model and leaves out (and logs) one that doesn't fit.
- Document the kind and its attributes in [entities.md](entities.md);
  `tests/test_entities_doc.py` fails until you do. [plugins.md](plugins.md) draws who
  publishes and reads what.
- **Linking kinds.** A kind that stands for a unit of work (a ticket, a work entry) is
  declared in `linking`, so the graph relates what's about it. Give it the pattern of its
  ids and other plugins' entities point at it from the text they mention:

  ```python
  linking: ClassVar[dict[str, str | None]] = {ISSUE: None}


  def linking_patterns(self, ctx) -> dict[str, str | None]:
      return {ISSUE: rf"\b{re.escape(ctx.config.team)}-\d+\b"}
  ```

  A publisher puts text that might name a unit of work in `Entity.mentions` (github does
  with a PR's branch, title and body), never refs to another plugin's kind.
- **Hiding kinds**, declared in `hides`, hide what their entities point at (notes' `iced`).
- `url_for(ctx, kind, id)` makes a link for an entity that isn't in the graph, and
  `fetch(ctx, kind, ids)` fetches entities by id from the tool now, for rare work like the
  daily prune.

## Reading other plugins' entities

Read another plugin's entities through a small model of your own, a view, listing only the
fields you use:

```python
class _PrView(Model):
    """What linear reads of a `pr` (github's)."""

    id: str
    state: str | None = None
    mine: bool


for pr in graph.view_related(Ref(kind=ISSUE, id=key), "pr", _PrView):
    ...
```

If the publisher renames an attribute, the view raises and your tests fail, instead of
the page quietly showing less. See [graph.md](graph.md#reading-it) for `view`, `view_one`,
`view_related`, `related`, `named`, `hidden` and `fetch`.

## Panels

```python
def panels(self, ctx, snapshot, graph) -> list[Panel]:
    return [
        Panel(
            key="assigned",
            priority=125,
            title="Linear issues",
            description="Issues assigned to me, oldest first.",
            items=[...],
        )
    ]
```

- Panel builders are pure: no I/O, no clock (use `ctx.now`), no database reads beyond what
  the plugin passes in. Anything slow belongs in `collect`.
- The page keys a panel `<plugin>.<key>` (`linear.assigned`) and groups a plugin's panels
  together. `priority` sets the default order, lowest first, in the skill's step order;
  the person can drag panels and groups about, and that wins.
- Each `Item` names its `subjects`, the entities it's about, main one first. Other plugins
  add their buttons by them, and core hides items whose subject is on ice.

### Act now

Core's Act now ranks what every plugin nominates. Mark a panel `act_now=True` and give it
default `signals`, and its items are nominated:

```python
Panel(..., act_now=True, signals=Signals(verb="Triage", effort="minutes"))
```

An item can carry its own `signals` (`effort`, `closes`, `waiting` and `waiting_since`,
`importance` from 0 to 2, `due`, `blocked`, `verb`), and a plugin can override
`act_now(ctx, snapshot, graph, panels)` to nominate precisely. Map your tool's own idea of
importance (a priority, an escalation) onto `importance`, in the plugin. An item whose main
subject is a unit of work (a linking kind, like a ticket) joins the best item about
something in that unit, so give such items a `verb` that reads well after another's.
Set `Item.fingerprint` to what would make a snoozed or dismissed item worth another look.

## Buttons, pages and commands

- **Actions.** `actions()` maps names to handlers `(ctx, graph, fields) -> str |
  ActionResult`; a button posts to `/actions/<plugin>/<name>`. Check every field: they
  come from a form, so treat them as untrusted (a repo must be configured, a worktree must
  be the repo's, an id must exist). Anything that deletes or rewrites sets `confirm`, and
  nothing force-pushes, resets or stashes. Raise `ActionError` with a message the page can
  show.
- **Buttons on other plugins' items.** `item_actions(ctx, item, graph)` adds buttons by an
  item's subjects; a plugin that reads its tables for them does so once per page in
  `prepare_item_actions(ctx, graph)`.
- **Keeping an item on the focus page** after an action changes it: return
  `ActionResult(notice, {KEPT: "<plugin>:<key>"})` and implement `kept_item`.
- **Pages.** `pages()` maps paths to handlers returning HTML (use `render.page` and
  `render.e` to escape everything). A page a tool's sign-in (OAuth) redirects back to goes
  in `sign_in_pages` too: browsers don't send the dashboard's cookie on a redirect from
  another site, so it's served without one, and must check OAuth's single-use `state`
  before doing anything.
- **Commands.** Set `cli_help` and implement `cli(parser)` for `mfd linear ...`.

## Upkeep

`prune(ctx, graph)` runs once a day: clear kept data about work finished more than
`ctx.prune_days` ago, and return what was done.

## Tests

Tests go in `tests/plugins/`, named after the plugin (`test_linear_*.py`).

- **Panel tests** build the page as core does, with `conftest.dashboard(...)` (add your
  snapshot to `conftest.stored`), and assert on panel contents.
- **Contract tests** assert on the entities the plugin publishes.
- Tests never touch the network: monkeypatch the client. Tests touching git make real
  repos in `tmp_path`.
- Make sure each test would fail without the change it covers.

## Docs to update in the same commit

- The plugin's page, `docs/plugins/<name>.md` (copy one of the others): its config and
  setup, what it publishes, its panels, what it nominates for Act now, its buttons, pages
  and commands. Add a row for it to the README's plugins table.
- [entities.md](entities.md) for its kinds, [plugins.md](plugins.md) for what it publishes
  and reads.
- `mfd init`'s rendered config and `mfd init --example` for its config keys.
