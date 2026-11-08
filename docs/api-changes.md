# Plugin API changes

What changed in `move_forward.api` for a plugin's author, newest first. A plugin declares
the version it's built against (`api_version = API_VERSION`); the dashboard leaves out one
built against another version, and says so in `mfd check` and the page's footer.

A change that could break a plugin built against the current version raises
`API_VERSION`: a name leaving `api`, a hook's request losing or retyping a field, a
`Plugin` attribute changing meaning, a stored format a plugin reads changing. Adding a
name, a hook, a request field or an optional `KindSpec` flag doesn't.

## Version 1

The first version. While the built-ins are its only plugins, changes go into it without
raising the version; it's listed so a plugin written against an earlier checkout can
catch up.

- **One person across tools**: `Graph.same_person(id)` and `view_same_person(id, model)` give the
  `person` entities with the same email. A publisher may set `Person.contact_type`, and point
  an entity at its person: core ranks and badges items about them.
- **Imports.** Core is `move_forward.api` (with `api.html`, `api.shell`, `api.http`,
  `api.localtime`, `api.keychain`, `api.kinds` and `api.ids`) and nothing else.
- **Hooks take one request object** (`PanelsRequest(ctx, snapshot, graph)` and so on).
  `ItemActionsRequest` says whose item it is and where it's shown; `ActionRequest` reads a
  required field with `req.field(name)`.
- **A failing hook** is logged and isolated; `collect` has a timeout
  (`collect_timeout_seconds`), and may collect in pieces (`Collected(snapshot, more)`).
- **`Context`** has `name`, `log`, `data_dir`, `kept(key)`, `part(name)` and
  `page_url(key)`; its kv has `delete`, `keys`, `get_json` and `set_json`.
- **Kinds** are declared with `KindSpec` (`linking`, `hides`, `fetchable`,
  `gone_means_done`, `resolvable`, `fetched_attrs`) on a `KindModel`, which rejects unknown
  attributes. A plugin's own kinds are named for it (`git.branch`); what more than one tool
  may represent is a family core models (`pull_request`, `ticket`, `person` in
  `api.kinds`), whose ids follow its rule (URLs; `<host>/<login>`) and whose `handle` is
  what text names it by. An entity says `lifecycle` (`open` or `done`).
- **Fetched entities** must say `lifecycle` and fit their kind's fetched model.
- **Act now**: a nominated item needs `Signals.task`, a stable id its snoozes and pins are
  kept by.
- **Buttons** post only to an action a plugin declares in `actions()`; others are left
  out. **Pages** are keyed relative to the plugin and served at `/<plugin>/<key>`; a page
  may return a `Response`; `sign_in_pages` names keys of `pages()`.
- **Conventions are hooks**: `refresh_seconds(ctx)`, `token_settings(config)`,
  `teams_field`, `collects`.
- **Config**: a plugin's section is `config.section(name)`.
- **Packaging**: other packages' plugins load from the `move_forward.plugins` entry point
  group; names in `RESERVED_NAMES` are refused.
- **Migrations** are named `Migration(name, run)`s, SQL or Python, kept by name; a plugin's
  tables are prefixed with its name.
- **Tests**: `move_forward.testing.Harness`.
- **Relations**: `Plugin.relate(req)` (`RelateRequest(ctx, graph)`) relates a plugin's
  entities to another's; github relates PRs to their local branches, read with
  `graph.related(branch, "pull_request")`. `git.branch` has `upstream`;
  `git_stack.stack_branch` has `stack`.
- **Page model**: a badge is a string or a `Badge(text, tone)` (`plain`, `warn`, `good`),
  and only a `warn` one is drawn as a warning (no more guessing from its words);
  `Link.done` strikes out a link to what's over (was `Link.pr_state`; `PrState` is gone);
  `Item.deferred` (was `Item.draft`) sorts an item last, and Act now leaves it out unless
  the plugin's `act_now` nominates it. A graph view also gets an entity's `lifecycle`.
- **Nested panel items**: `Item.parent_anchor` names another item in the same panel by
  its stable `anchor`. The page nests it below that item. Parents with children get a
  collapsible section; `children_open` sets its initial state and `children_title` names it.
- **Importance order**: `Panel.sort_by_importance` sorts its items first by user-assigned
  importance on their subjects (or `Signals.importance`), then by `Item.rank`.
- **Importance choices**: core adds its three priority buttons to every item with a subject,
  including focus-mode parts, targeting the first subject, unless that's a kind that `hides`. `Action`s with the same
  `group` render as one button group, even when other actions separate them;
  `Action.selected` highlights a choice and `Action.icon`
  shows an emoji instead of visible text, with the unchanged label in `title` and
  `aria-label`.
- **Parts in Act now**: Act now lists an item's `parts` when there are several, as the dashboard
  does, and an item's details show alongside them. Don't repeat parts in `details`.
- **Due dates in Act now**: `ActNowRequest.due_horizon` says how far ahead a due date counts
  (`[act_now] due_warning_days`). A plugin nominating by due date passes it to `due_pressure`.
- **A drop-down beside a text field**: `Action.choices` adds one, label to value, posted as
  `choice`; it needs an `input`.
- **Choices for a text field**: `Action.suggestions` lists what an `input` offers to pick
  from; anything else can still be typed, so check the posted field as usual.
- **Snoozing in a panel**: `Panel.snoozable`, on a panel whose items are `act_now` ones, gives
  each a Snooze button and keeps those snoozed or dismissed off the panel, the same as in
  Act now.
- **Fetching some entities again**: `Plugin.refresh_subjects(req)`
  (`SubjectsRequest(ctx, refs, snapshot)`) returns `Subjects(snapshot, updated)`, or None
  to be refreshed whole; `subjects_timeout_seconds`. An action's `touched` refs are
  fetched this way, not by refreshing their publishers whole.
- **Opened items**: `Item.pending` is gone; core sets `Item.act_now_fields` and
  `Item.opened_at` on Act now's items (a plugin's are ignored). Act now's feedback action
  `pending` is `opened`.
- **Panel order**: `Band` names where a panel goes in the day's check-in
  (`priority=Band.CI`); Act now no longer reads `Item.rank`, which orders an item within
  its panel only.
