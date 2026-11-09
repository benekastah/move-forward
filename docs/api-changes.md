# Plugin API changes

What changed in `move_forward.api` for a plugin's author, newest first. A plugin declares
the version it's built against (`api_version = API_VERSION`); the dashboard leaves out one
built against another version, and says so in `mfd check` and the page's footer.

A change that could break a plugin built against the current version raises
`API_VERSION`: a name leaving `api`, a hook's request losing or retyping a field, a
`Plugin` attribute changing meaning, a stored format a plugin reads changing. Adding a
name, a hook, a request field or an optional `KindSpec` flag doesn't.

## Version 2

Fewer things to know: what no plugin used is gone.

- **No collecting in pieces**: `Collected` has left `api`. `collect` and `collect_after`
  return the snapshot, and core calls each once per refresh.

## Version 1

The first version. While the built-ins are its only plugins, changes go into it without
raising the version; it's listed so a plugin written against an earlier checkout can
catch up.

- **A checkout's git directory**: `api.shell.git_common_dir(repo)` is the directory holding a
  checkout's refs and config (its `.git`, or a linked worktree's main repository's), read
  from the files without running git; None when `repo` isn't a checkout.
- **The dashboard's URL**: `Context.dashboard_url` is always `http://localhost:<port>`, as the
  dashboard no longer serves HTTPS.
- **Who may hide by text**: `Entity.mentions_hide` (True by default) says whether a unit of
  work named only in its `mentions` can hide it, as an iced ticket hides a PR naming it. Set
  it False where someone else wrote the text (a PR I'm asked to review). What the text names
  is still linked to it.
- **A tag's handle**: `api.tags.handle(text)` is the tag core makes of a label or a typed tag
  (`Garbage Day` → `garbage-day`), so a plugin can tell its own labels' tags from the others
  on an entity.
- **Settings a plugin locks**: `Plugin.locked_settings` names the settings the settings page
  shows but won't change (a site, a host), and a section's `token_env` and
  `keychain_service` are locked without naming them (other settings a token's place is read
  from or built from, the plugin names). `Plugin.token_help` is `mfd init`'s next
  step for its token (where to get one, another way to sign in).
- **Names and loads**: a plugin named for a prefix of one of core's tables or indexes (`team`
  of `team_channels`) is refused, and a plugin's migration may not touch another plugin's
  tables whose name starts with its own (`git_stack_` for `git`). Core holds the database
  while a plugin's `load` runs, so a refresh can't store between its queries: a `load` mustn't
  wait on another thread using `ctx.db`. `Database.transaction()` within another, on the same
  thread, raises rather than committing the outer one's writes.
- **A local time on a day**: `api.localtime.at(day, time)` gives the local clock time on a
  day, at that day's offset, so a time across a summer-time change keeps its clock time.
- **Several tokens**: `Plugin.tokens(config)` names every token a plugin keeps, `""` for its
  own (by default `token_settings`'s) and one per account for a plugin reading several;
  `mfd token set <plugin>.<account>` stores one, and the config is refused when two tokens,
  whoever's, share a `keychain_service`, whatever its case.
- **What a token setting is**: `api.keychain.TokenSettings`, anything with a `token_env` and a
  `keychain_service` (read-only), so a plugin can annotate `token_settings` and return frozen
  settings. The config is refused when two enabled plugins' `keychain_service`s are the same,
  as storing one token would replace the other's.
- **Organisations**: core declares the `organisation` family (`api.kinds.ORGANISATION`,
  `Organisation`: `handle`, `contact_type`, `importance`), with ids `<host>/<its id there>`.
  A person points at theirs, and its type or importance counts for that person's items as
  their own would.
- **What's done, kept to undo**: `Item.done` marks an item or part whose work is done but
  is kept a while (a todo ticked off today): it's shown dimmed, isn't counted, and focus mode
  doesn't step through it.
- **A label's badge**: `Badge(tone="label")` colours a tag its tool put on an item, which only
  the tool takes off.
- **Labels as tags**: `Entity.tags` lists the tags a tool puts on an entity (a ticket's or a
  PR's labels, as written there); core counts them as `#tags`, whatever the kind's
  `tags_from_text`, and names a tag only labels give only by its `#`. Only labels no outsider
  can set belong there (people with a say in the tool, or the tool itself); the plugin decides
  whether the tool's own (Gmail's `\Inbox`) do.
- **Tags**: core publishes a `tag` (`api.kinds.TAG`) for each `#tag` in an entity's title or
  `mentions` (out of code), pointing at what carries it; an entity whose text names one points
  at it. A kind whose text anyone can write says `KindSpec(tags_from_text=False)`. No plugin may
  publish `tag` or name a family so.
- **Families a plugin declares**: `Plugin.families` declares a family core doesn't model
  (`api.kinds.Family`), whose kinds the plugin, and others declaring the same family,
  publish as they would core's; a plugin publishes kinds only of core's families and its
  own. A family's model needs a `handle`. `api.kinds.is_url` and `is_host_login` are core's
  id rules, for a family to use. One declared differently from core or another plugin
  leaves its plugin out.
- **How much a person matters**: `Person.importance` (0 to 2) lets a tool say it outright, a
  CRM's VIP or a starred contact, rather than through a type and `[people] contact_types`. Core
  raises the items about them by the highest of it and their type's.
- **A single part listed**: `Item.parts_listed` lists an item's `parts` even when there's
  one. Core sets it in Act now, on an item showing the parts of one joined to it.
- **One person across tools**: `Graph.same_person(id)` and `view_same_person(id, model)` give the
  `person` entities with the same email. A publisher may set `Person.contact_type`, and point
  an entity at its person: core ranks and badges items about them.
- **Imports.** Core is `move_forward.api` (with `api.html`, `api.shell`, `api.http`,
  `api.localtime`, `api.keychain`, `api.kinds`, `api.ids` and `api.tags`) and nothing else.
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
- **Importance choices**: core adds its priority buttons (Important, Critical) to every item with a subject,
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
- **Snoozing in a panel**: every item whose `signals` (or its panel's) have a `task` gets Snooze
  and Dismiss in its panel (`act_now.set_aside_actions`). What's snoozed or dismissed, there or
  in Act now, stays in its panel, last and dimmed (`Item.set_aside`), with Unsnooze or
  Undismiss; Act now leaves it out.
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
