# Working on move-forward

Notes for agents (and people) changing this repo. The README says what the
dashboard does; `docs/entities.md` is the contract between plugins,
`docs/graph.md` explains the graph they go through, `docs/plugins.md`
draws how the plugins use each other's entities, and `docs/writing-a-plugin.md`
walks through making one; `plan.md`
is what's left to build.

## Shape of the code

- Core (`src/move_forward/`) knows no tool. It loads the config, enables the
  plugins whose section is there (`registry.py`), runs their collectors,
  builds the entity graph (`graph.py`), and renders the page.
- Each tool is a plugin in `src/move_forward/plugins/<name>/`: `config.py`
  (its section's model), `models.py` (its data), `client.py` (talking to the
  tool), a repository module with its tables' migrations and queries,
  `panels.py` (pure functions from its snapshot and the graph to panels),
  `plugin.py` (the `Plugin` subclass), and `actions.py` if it has buttons.
  Core's `domain.py` has only the base `Model`.
- Plugins never import each other or read each other's config, snapshots or
  tables. What one needs from another goes through the graph. A plugin that's
  built on another declares it in `requires`.
- A plugin declares the kinds it publishes in `kinds`, each with a model of its
  attributes, and the graph checks its entities against them. A consumer reads
  another plugin's entities with `graph.view(kind, View)`, through its own
  small model of the fields it uses, not `attrs.get`. A kind that stands for a
  unit of work (a ticket, a work entry) is declared in `linking` (with
  `linking_patterns` narrowing its id pattern from the plugin's data); an entity
  that may name one publishes the text in `mentions` rather than refs to
  another plugin's kind. A kind whose entities hide what they point at (ice) is
  declared in `hides`, and core leaves items about hidden things off the page;
  plugins don't filter them out themselves.
- A plugin keeps what `collect` fetched in its own tables, with `store`, and
  reads it back with `load`; nothing is kept as JSON. Core keeps only when each
  plugin last fetched, and why it failed.
- Panel builders are pure: no I/O, no clock (use `ctx.now`), no database
  reads beyond what the plugin passes in. Everything slow happens in
  `collect` or `after_collect`, or in `collect_after` for a plugin that
  needs other plugins' entities to know what to fetch. Rare work (the daily
  prune) can ask the owning plugin for entities its snapshot no longer holds
  with `graph.fetch`. Buttons on other plugins' items come from
  `item_actions`, called per item; a plugin that reads its tables for them
  does so once per page in `prepare_item_actions`. A plugin nominates items for
  core's Act now with `act_now`, each with `Signals` (effort, what it closes,
  who's waiting); by default, the items of its panels marked `act_now`.

## Rules for changes

- **One kind of change per PR.** Don't change core in the PR that adds or
  changes a plugin. If a plugin needs something new from core (a hook, a
  `Context` field, a graph method), land that first, on its own, with its own
  tests, and stack the plugin PR on it (`git stack branch`).
- **Keep the docs up to date as you code**, in the same commit as the change,
  not in a docs pass afterwards:
  - the README: panels, config keys, commands, command-line options and HTTP
    endpoints. The available plugins table and the API section must match the
    code.
  - `docs/entities.md`: a new entity kind or attribute, or a change to what
    one means or who publishes it. `tests/test_entities_doc.py` checks its
    kinds and attributes against the plugins' kind models.
  - `docs/plugins.md`: a change to what a plugin publishes or reads, its item
    actions, its `requires`, or the phase it collects in.
  - `docs/graph.md`: a change to how the graph is built or read (its methods,
    views, kinds, `related`, `fetch`), or to the kinds in its overview diagram.
  - this file, when the shape of the code or a rule here changes, e.g. a new
    plugin hook.
  - docstrings and comments on the code you touch.
  - `plan.md`: remove what the change finishes, whether a step, a bullet or a
    design section, and delete the file once nothing is left.

  Before committing, look for docs the change made untrue: search the docs
  and docstrings for the names you renamed, moved or removed.
- **Every config key appears in four places**: its model (with a docstring),
  the README, `mfd init`'s rendered config (as a commented default, see
  `init.py`) and `mfd init --example` (`cli.py`). Config models reject unknown
  keys; keep it that way. A key that moves says where it went (see
  `MOVED_SETTINGS` in `config.py`).
- **Migrations are append-only.** Core and each plugin own a list of SQL
  scripts applied in order and counted in `schema_versions`. Never edit one
  that has been committed to `main`; add a new one. A plugin's tables are
  prefixed with its name.
- **A plugin table is fetched or kept**, and its comment says which.
  Fetched tables hold what `collect` brings back; `store` replaces them
  wholesale, so they're never pruned. A table computed from the graph in
  `after_collect` (github's reviewer suggestions, notes' ice covers) is
  replaced the same way, and counts as fetched (or says "derived"). Kept tables hold what only the dashboard
  knows (notes, pings sent, looked-up emails); they survive refreshes, a
  migration that reshapes them copies their rows, and the plugin's `prune`
  clears what's finished.
- **Plugin migrations don't depend on order.** A plugin can be enabled at any
  time, before or after any other, so its migrations touch only its own tables
  and never assume another plugin's migrations have run, or haven't. Data
  moving out of core into a plugin is copied by the plugin's own migration,
  from core's table if it's still there; core never writes into a plugin's
  tables.
- **Add tests** with every change, and make sure they would fail without it.
  A plugin's tests go in `tests/plugins/`, named after it (`test_github_*.py`),
  as do tests of plugins working together (`test_actions.py`); core's stay in
  `tests/`.
  Panel tests build the page as core does (`conftest.dashboard(...)`) and
  assert on panel contents; plugin contract tests assert on the entities a
  plugin publishes. Tests touching git make real repos in `tmp_path`; tests
  never touch the network.
- **No new runtime dependencies.** pydantic is the only one; tools are
  reached through their CLIs (`gh`, `git`) or the standard library.
- **Nothing destructive without a confirm.** Buttons that delete or rewrite
  set `confirm`, never force-push, reset or stash.
- **Every save must leave working code.** People run `mfd serve --reload`,
  which restarts on each change. Each file you write must be syntactically
  valid, and every import in the package must still resolve: when moving or
  renaming something, update its importers in the same edit, or add the new
  name before removing the old one. The reloader checks with `mfd check`
  before restarting and waits out a failure, but until the code is fixed the
  page runs the old code.

## Before committing

```sh
./check.sh            # ruff format --check, ruff check, basedpyright (strict), pytest
```

It must pass on every commit, not just the last one in a PR. Commit in small,
logical steps with messages that say why, not just what.
