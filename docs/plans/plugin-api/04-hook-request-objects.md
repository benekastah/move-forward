# 04 — One request object per hook

Groups problems 3 and 10: the missing inputs in 10 are added as fields of the request
objects 3 introduces, in the same PRs, so there is no second signature change.

## Problem 3: hook signatures are positional and grow by breaking subclasses

`events(ctx, before, after)` (`plugin.py:154`), `graph_events(ctx, graph)` (`:163`),
`panels(ctx, snapshot, graph)` (`:226`), `act_now(ctx, snapshot, graph, panels)` (`:229`),
`item_actions(ctx, item, graph)` (`:243`), `prepare_item_actions(ctx, graph)` (`:247`),
`kept_item(ctx, graph, key)` (`:252`), `changed(ctx, graph)` (`:187`), handlers `(ctx,
graph, fields)` (`:87`), `(ctx, graph, query)` (`:68`), `(ctx, graph, args)` (`:70`).
Giving `events` the graph today changes every subclass. **Before**: every later addition
would otherwise be a break.

## Problem 10: hooks lack what they need

- `events` has no graph (`refresh.py:55`): github can't say "the PR of ticket X merged".
- `graph_events` has no previous graph, so jira and git keep diffs in kv
  (`jira/plugin.py:332-337`, `git/plugin.py:238` via `events.deletable(ctx.kv, …)`).
- `item_actions` doesn't know which surface (dashboard, focus, act now) or whose item, and
  can only add buttons (`panels.py:217-228`); notes can't add a "has 2 todos" badge.
- `kept_item` rebuilds from a bare string.
- Actions get fields only; `App.run_action` refreshes **every** plugin afterwards
  (`server.py:335-336`), so ticking a todo runs github's twenty searches.
- `changed` returns a bare bool; the log says "changed: github; refreshing" and nothing of
  why (`server.py:205,243`).
- `me()` is first-plugin-wins (`refresh.py:224`).

**Before** for everything above (cheap once the request objects exist). Persisting the
previous graph across restarts is deferred (memory only here).

## PR 4a — read hooks

Frozen dataclasses in `plugin.py`, exported from `api`:

- `EntitiesRequest(ctx, snapshot)`, `PanelsRequest(ctx, snapshot, graph)`,
  `ActNowRequest(ctx, snapshot, graph, panels)`, `FetchRequest(ctx, kind, ids)`,
  `UrlRequest(ctx, kind, id)`, `LinkingRequest(ctx)`, `KeptItemRequest(ctx, graph, key)`.
- `ItemActionsRequest(ctx, item, graph, owner: str, surface: Literal["dashboard", "focus",
  "act_now"])`; `item_actions` and the callable from `prepare_item_actions` return
  `ItemAdditions(actions=[], badges=[], details=[], links=[])`; `panels._with_item_actions`
  merges all four. No shim for a bare `list[Action]`: one user, break it.
- `me` → `identity(IdentityRequest(ctx, snapshot)) -> Identity(login, tool) | None`;
  `Dashboard.login` → `identities: list[Identity]`; footer lists each ("github: me ·
  jira: me@x"); `mfd dump` output changes accordingly.
- Built-ins and `tests/test_plugin_hooks.py` converted.

**Tests.** Badges from `ItemAdditions` reach the rendered item; `surface` is `focus` on the
focus page; two identities in the footer. **Size.** L.

## PR 4b — refresh hooks

- `CollectRequest(ctx)`, `CollectAfterRequest(ctx, graph)`, `StoreRequest(ctx, snapshot)`,
  `LoadRequest(ctx)`, `AfterCollectRequest(ctx, graph)`, `PruneRequest(ctx, graph)`.
- `EventsRequest(ctx, before, after, graph)`: `_store` (`refresh.py:41-58`) gets the graph
  built before the collect phase (already in hand at `refresh.py:147` for `collect_after`;
  for phase one, build it once before `_collect_all`).
- `GraphEventsRequest(ctx, graph, previous: Graph | None)`: `App` keeps the last graph it
  built and passes it through `refresh(..., previous=)`; `None` on the first run and for
  `mfd refresh`. jira's `CLOSEABLE` kv and git's `deletable` kv can go once they diff graphs.
- `ChangedRequest(ctx, graph)` → `bool | Changed(changed, reason: str, subjects: list[Ref])`;
  the reason joins the "changed: …; refreshing" log lines.

**Tests.** `events` sees the graph; `graph_events` gets `previous` on the second refresh and
`None` on the first; reason appears in the log. **Size.** L.

## PR 4c — handlers

- `ActionRequest(ctx, graph, fields, surface, back: str | None)` with `req.field(name)`
  raising `ActionError("missing field …")` (finishes 01's `KeyError` fix).
- `ActionResult.refresh: frozenset[str] | None = None`: which plugins to refresh after;
  `None` = the acting plugin only (today: all). git's branch actions → own; notes' →
  own; jira's transition → own; slack's `mark_sent` → own. `App.run_action` honours it.
- `PageRequest(ctx, graph, query, path)`; handlers return `str` here (`Response` comes in
  07). `CliRequest(ctx, graph, args)`.
- `plan.md`'s "class variables to the top" done while every plugin file is open.

**Tests.** Existing action tests adapted; `test_server.py:224` asserts only the acting
plugin is refreshed; a page handler sees its path. **Size.** L.

**Migration/compat (all three).** None stored. **Risk.** Medium: touches every plugin file,
but mechanically; three PRs so each keeps `--reload` green.

## Decisions

- Request objects (**recommended**) vs keyword-only parameters (allow additions, not
  removals or defaults derived from other inputs) vs a fatter `Context` (mixes per-plugin
  environment with per-call inputs).
- `ActionResult.refresh` default **own plugin only**; the alternative (all, as today)
  makes every click cost a full github collect.
- Footer lists every identity (**recommended**) vs a `login_plugin` setting.

## Dependencies

02 (exports), 03 (`ctx.log`, `ctx.name` used in request builders). 05, 07, 08 build on
these types.
