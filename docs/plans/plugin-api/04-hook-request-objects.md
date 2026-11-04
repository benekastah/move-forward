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

**Before** for everything above (cheap once the request objects exist).

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
- `GraphEventsRequest(ctx, graph, snapshot, previous_graph, previous_snapshot)`: what held
  before this refresh, so a notification fires when something becomes true, not every
  refresh while it stays so. git's and jira's events come from their snapshot and the
  graph together, so both are passed. "Before" is what was stored before collecting: after
  a start, what the dashboard knew when it stopped. `refresh` builds it first, so nothing
  is kept in memory or stored. jira's `CLOSEABLE` kv and git's `deletable` kv go.
- `ChangedRequest(ctx, graph)` → `bool | Changed(changed, reason: str, subjects: list[Ref])`;
  the reason joins the "changed: …; refreshing" log lines.
- Collecting in pieces: `collect` may return `Collected(snapshot, more=True)` for work too
  big for one `collect_timeout_seconds` (01). Core stores the piece as any collect, then
  runs the plugin's `collect` again right after the other due plugins, until `more` is
  False; each piece has the timeout to itself. The plugin keeps its place in kv and merges
  each piece into what it loads, as git's partial read and github's targeted fetch do now
  (`git/plugin.py`'s `collect`, `github/plugin.py`'s `collect`). A plain snapshot means
  `more=False`.

**Tests.** `events` sees the graph; `graph_events` gets the last graph on the second
refresh and the stored one on the first, so a branch that became deletable while the
dashboard was stopped notifies once; reason appears in the log; a collect returning
`more=True` twice runs three times in one refresh, each piece timed on its own.
**Size.** L.

## PR 4c — handlers

- `ActionRequest(ctx, graph, fields, surface, back: str | None)` with `req.field(name)`
  raising `ActionError("missing field …")` (finishes 01's `KeyError` fix).
- `ActionResult.touched: list[Ref] = []`: what the action changed outside its own plugin.
  `App.run_action` refreshes the acting plugin, and the plugin that published each touched
  ref (a family's ref goes to the plugin whose entity has the id, 05); once 14 lands, just
  those items. Today it refreshes every plugin. An action names what it acted on, not
  plugins: slack reacting on a PR knows it touched a PR, not who publishes PRs. Built-ins:
  git's branch actions, notes', jira's transition and slack's `mark_sent` touch nothing
  outside themselves.
- `PageRequest(ctx, graph, query, path)`; handlers return `str` here (`Response` comes in
  07). `CliRequest(ctx, graph, args)`.
- `plan.md`'s "class variables to the top" done while every plugin file is open.

**Tests.** Existing action tests adapted; `test_server.py:224` asserts only the acting
plugin is refreshed; an action touching a PR refreshes its publisher too; a page handler
sees its path. **Size.** L.

**Migration/compat (all three).** None stored. **Risk.** Medium: touches every plugin file,
but mechanically; three PRs so each keeps `--reload` green.

## Decisions

- Request objects (**recommended**) vs keyword-only parameters (allow additions, not
  removals or defaults derived from other inputs) vs a fatter `Context` (mixes per-plugin
  environment with per-call inputs).
- After an action, **refresh the acting plugin and the publishers of what it touched**
  (`touched: list[Ref]`) vs naming plugins (`refresh: frozenset[str]`: the action would
  have to know who publishes a PR) vs all, as today (every click costs a full github
  collect).
- Footer lists every identity (**recommended**) vs a `login_plugin` setting.

## Dependencies

02 (exports), 03 (`ctx.log`, `ctx.name` used in request builders). 05, 07, 08 build on
these types.
