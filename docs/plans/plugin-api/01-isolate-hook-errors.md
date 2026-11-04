# 01 — Isolate every hook; time out `collect`

## Problem 6: one failing hook takes the page, and the refresh, down

Guarded today: `collect`/`store` (`plugin.py:327`), `events` (`refresh.py:57`),
`graph_events` (`:175`), `after_collect` (`:161`), `prune` (`:209`), `changed`
(`server.py:217`). Unguarded:

- `load`, `entities`, `linking_patterns` inside `stored_graph` (`registry.py:151,183`,
  `refresh.py:37`). It runs on every page and inside `refresh()` (`refresh.py:147`), so one
  `entities()` that raises 500s every page **and** stops every plugin's refresh, not just
  the culprit's.
- `panels`, `act_now`, `me`, `prepare_item_actions` and its per-item callable
  (`refresh.py:221-233`): a bug in one panel builder blanks the dashboard.
- Page handlers (`refresh.py:264`), `kept_item` (`:283`).
- Actions: a plugin's own `KeyError` is reported as "missing field" (`refresh.py:382`),
  hiding bugs.
- `collect` has no timeout: `_collect_all` waits for the pool (`refresh.py:76`) while
  `App.refresh_now` holds `_refresh_lock` (`server.py:168-181`). One hung `gh` call (or a
  third party's HTTP client without a timeout) ends all refreshing until restart.

**Before inviting others**: with code you didn't write in the process, this is the first
failure you will see, and it's the one that makes the dashboard look broken rather than one
plugin.

## PR 1 — isolate and time out

**Goal.** No plugin can take down the page, another plugin's refresh, or the refresh loop.

**Changes.**
- `registry.py`: `call(e, hook, fn, default)` catches `Exception`, logs with `exc_info`
  once per `(plugin, hook)` per 10 minutes (an in-memory throttle), returns `default`. Use
  it in `build_graph` for `entities`, `linking_patterns`, `url_for`; in `stored_graph` for
  `load`; in `build_dashboard` for `panels`, `act_now`, `me`, `prepare_item_actions` and the
  per-item callable.
- A failing `panels` yields one panel `"<title> (failed)"` whose description is the error,
  so the user sees it. A failing `entities` leaves that plugin's entities out; the graph
  still builds and the other plugins still refresh.
- `plugin_page` / `kept_item` / `perform_action`: wrap the handler. A page handler that
  raises answers 500 for that path only (`server.py:578`); an action raises
  `ActionError(f"{name} failed: {type(e).__name__}: {e}")`. Stop mapping arbitrary
  `KeyError` to "missing field": keep that only around the field access (subplan 04 adds
  `req.field()`; until then, a `_field(fields, name)` helper in `refresh.py`).
- `Plugin.collect_timeout_seconds: ClassVar[int] = 300`. In `_collect_all`, submit then
  `future.result(timeout=...)`; on `TimeoutError`, `save_error(source, now, "timed out
  after Ns")` and move on; `pool.shutdown(wait=False)`. Threads can't be killed, so `App`
  keeps a "still collecting" set and skips a plugin whose previous collect hasn't returned
  (log once); `collect_into` clears it in `finally`.
- Note in the PR (not changed): `prune_if_due` and `graph_events` still run under the
  refresh lock; a prune calling `graph.fetch` holds it for a network round trip.

**Migration/compat.** None.

**Tests.** `entities` raises → page builds, its kinds absent, one log line; `panels`
raises → failure panel present; page handler raises → 500 and `/` still 200; action
raises `RuntimeError` → notice says failed, not "missing field"; `collect` sleeping past a
0.1 s timeout → source error recorded, the other plugin collected, lock released, second
refresh skips the stuck plugin.

**Risk.** Low. **Size.** M.

## Decisions

- Collect timeout default: **300 s** (git's full read of several repos can take a minute,
  github's twenty searches two), overridable per plugin. Alternative: no default and
  require each plugin to set one; worse for the author.

## Dependencies

None. First in the stack so the refactors that follow can't blank the page while
`--reload` picks up a half-finished built-in.
