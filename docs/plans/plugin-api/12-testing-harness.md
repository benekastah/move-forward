# 12 — `move_forward.testing`

## Problem 16: nothing an out-of-tree plugin's tests can import

`tests/conftest.py` is built around the six built-ins: `stored()` (`conftest.py:141-237`)
hard-codes github and git as always on and knows every plugin's overrides; `dashboard()`,
`pings()`, `know_jira_projects()` likewise; `git_polls` monkeypatches `registry.PLUGINS`
internals (`conftest.py:53-60`). None of it ships in the package. An author writing
`test_linear_panels.py` starts from a blank `Database(":memory:")`, re-implements
`enabled()` and `build_graph`, and gets a different page than core would build.

**Before**: "write tests" is in the docs (`writing-a-plugin.md:286-295`); without this they
can't follow it.

## PR 12 — the harness

**Changes.**
- Package `move_forward.testing` (no pytest import; plain callables, so authors wrap them in
  their own fixtures):
  - `Harness(plugin, section: dict, *, now=NOW, email="me@x", teams=[])`: an in-memory
    `Database` migrated for core and the plugin, a `Context`, and the plugin enabled alone.
  - `harness.with_entities([...Entity])`: seeds other kinds through a stub publisher
    plugin, so a consumer test can place a `pr` without github.
  - `harness.entities()`, `panels()`, `act_now()`, `dashboard()` (as `build_dashboard`
    does, with Act now), `run_action(name, fields)`, `page(path, query)`,
    `graph_events()`, `prune()`; `render_item(item)` for HTML assertions.
  - `harness.store(snapshot)` as a refresh would; `harness.tick(seconds)` moves `now`.
- Port `tests/plugins/test_jira_panels.py` to it as the worked example; the rest of
  `conftest.py` stays (it tests the built-ins together).
- `docs/writing-a-plugin.md` "Tests" section rewritten around it.

**Migration/compat.** None. **Tests.** The harness's own (a stub plugin end to end) plus the
ported module. **Risk.** Low. **Size.** M.

## Decisions

- Ship it in the package (**recommended**: the author's `pip install move-forward` has it)
  vs a separate `move-forward-testing` distribution.

## Dependencies

09 (constructs plugins without the registry list; `Config.section`). After 10b, add
`with_relations`.
