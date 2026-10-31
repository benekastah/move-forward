# Working on move-forward

Notes for agents (and people) changing this repo. The README says what the
dashboard does; `docs/entities.md` is the contract between plugins;
`plugin_rearchitecture.md` is the plan the code is being moved towards.

## Shape of the code

- Core (`src/move_forward/`) knows no tool. It loads the config, enables the
  plugins whose section is there (`registry.py`), runs their collectors,
  builds the entity graph (`graph.py`), and renders the page.
- Each tool is a plugin in `src/move_forward/plugins/<name>/`: `config.py`
  (its section's model), `client.py` (talking to the tool), `panels.py` (pure
  functions from its snapshot and the graph to panels), `plugin.py` (the
  `Plugin` subclass), and `actions.py` if it has buttons.
- Plugins never import each other or read each other's config, snapshots or
  tables. What one needs from another goes through the graph. A new entity
  kind or attribute, or a change to what one means, updates
  `docs/entities.md` in the same commit.
- Panel builders are pure: no I/O, no clock (use `ctx.now`), no database
  reads beyond what the plugin passes in. Everything slow happens in
  `collect` or `after_collect`.

## Rules for changes

- **One kind of change per PR.** Don't change core in the PR that adds or
  changes a plugin. If a plugin needs something new from core (a hook, a
  `Context` field, a graph method), land that first, on its own, with its own
  tests, and stack the plugin PR on it (`git stack branch`).
- **Update the README as you go**, in the same commit as the change: new or
  changed panels, config keys, commands and HTTP endpoints. The available
  plugins table and the API section must match the code.
- **Every config key appears in four places**: its model (with a docstring),
  the README, `mfd init`'s rendered config (as a commented default, see
  `init.py`) and `mfd init --example` (`cli.py`). Config models reject unknown
  keys; keep it that way. A key that moves says where it went (see
  `MOVED_SETTINGS` in `config.py`).
- **Migrations are append-only.** Core and each plugin own a list of SQL
  scripts applied in order and counted in `schema_versions`. Never edit one
  that has been committed to `main`; add a new one. A plugin's tables are
  prefixed with its name.
- **Add tests** with every change, and make sure they would fail without it.
  Panel tests build the page as core does (`conftest.dashboard(...)`) and
  assert on panel contents; plugin contract tests assert on the entities a
  plugin publishes. Tests touching git make real repos in `tmp_path`; tests
  never touch the network.
- **No new runtime dependencies.** pydantic is the only one; tools are
  reached through their CLIs (`gh`, `git`) or the standard library.
- **Nothing destructive without a confirm.** Buttons that delete or rewrite
  set `confirm`, never force-push, reset or stash.
- **Keep the package importable after every save.** People run
  `mfd serve --reload`, which restarts on each change; a half-moved import
  takes the page down until the next save.

## Before committing

```sh
./check.sh            # ruff format --check, ruff check, basedpyright (strict), pytest
```

It must pass on every commit, not just the last one in a PR. Commit in small,
logical steps with messages that say why, not just what.
