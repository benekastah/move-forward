# Plugin API changes

What changed in `move_forward.api` for a plugin's author, newest first. A plugin declares
the version it's built against (`api_version = API_VERSION`); the dashboard leaves out one
built against another version, and says so in `mfd check` and the page's footer.

A change that could break a plugin built against the current version raises
`API_VERSION`: a name leaving `api`, a hook's request losing or retyping a field, a
`Plugin` attribute changing meaning, a stored format a plugin reads changing. Adding a
name, a hook, a request field or an optional `KindSpec` flag doesn't.

## Version 2

Fewer things to know: what no plugin used is gone, and what several plugins wrote each
for themselves (a token, a request, an example config) is core's.

- **One `address` hook**: `url_for(req)` and `handle_for(req)` (and `UrlRequest` and
  `HandleRequest`) are one hook, `address(req: RefRequest)`, answering a URL and a handle for
  each of `req.ids` it knows as an `Address(url, handle)`, by id. By default it gives the id as
  the URL where its family's ids are URLs, as `url_for` did.
- **Five request types**: a hook's request is one of `ContextRequest(ctx)` (`load`,
  `linking_patterns`), `Request(ctx, graph, snapshot, previous, panels)` (`store`,
  `entities`, `relate`, `panels`, `act_now`, `identity`, `item_actions`, `after_collect`,
  `prune`, `events`), `RefreshRequest(ctx, graph, full, subjects, previous)` (`collect`,
  `changed`), `RefRequest(ctx, kind, ids)` (`address`, `fetch`) and `InputRequest(ctx,
  graph, page, form, args)` (pages, buttons and `mfd <plugin>`), in place of a type per hook
  (`EntitiesRequest`, `PanelsRequest`, `CollectRequest`, `ChangedRequest`, `ActionRequest`,
  `PageRequest`, `CliRequest` and the rest, named in the entries below, are gone). A field a
  hook doesn't get keeps its default. Every hook with a snapshot now has the graph and the
  snapshot both: `entities` gets an empty graph, as the graph is built from it, and `store`
  gets what `collect` fetched as `req.snapshot` (never None there). A page's query and path
  are `req.page.query` and `req.page.path` (`Page`), a button's fields, surface and back
  `req.form.fields`, `req.form.surface` and `req.form.back` (`Form`); `req.field(name)` still
  reads a required field.
- **`Previous`**: `changed` and `events` get what held before as `req.previous`: `at` (when
  the last check or refresh ran), `fingerprint` (the last check's answer), `snapshot` (the
  plugin's before this refresh) and `graph` (the graph then; None at the first check since
  the dashboard started). It replaces `ChangedRequest`'s `previous_graph`, `fingerprint` and
  `checked_at`, and `EventsRequest`'s `before` and `previous_graph` (`after` is
  `req.snapshot`).
- **The due horizon is the context's**: `ctx.due_horizon`, as `[act_now] due_warning_days`
  says, in place of `ActNowRequest.due_horizon`.

- **One item-actions hook**: `prepare_item_actions` is gone, and `item_actions(req)` has its
  shape: called once per page with `ItemActionsRequest(ctx, graph)`, it returns the function
  (`ItemAdditionsFor`) core calls for each item with an `ItemRequest(item, owner, surface)`.
  A plugin that implemented the per-item `item_actions` wraps its body in a function it
  returns; one that implemented `prepare_item_actions` renames it, and its function takes an
  `ItemRequest`.
- **Asked only about its own kinds**: core calls `url_for`, `handle_for` and `fetch` only on
  the plugins whose `kinds` include `req.kind` (about a family, each plugin that publishes
  it), so a plugin
  drops its `if req.kind != ...` check. `url_for` gives the id by default where its family's
  ids are URLs (a pull request's, a ticket's), so a plugin whose `url_for` only did that
  drops it.
- **Core keeps what moved**: `changed` says what moved in the tool, and core keeps it, not
  the plugin. `Changed` takes keywords only: `fingerprint` (the tool's state as text, which
  core compares with the last), `subjects` (what moved since the last check, in the
  plugin's own terms),
  `everything` (something moved that no subject names; it was `changed`) and `reason`;
  `Changed()` says nothing moved, and an answer is no longer true or false. `ChangedRequest` gains `fingerprint` and
  `checked_at`, the last answer's fingerprint and when it was taken, and `CollectRequest`
  and `CollectAfterRequest` gain `subjects` (what moved since a collect last stored, which
  core keeps until a collect started after it stores) and `full` (whether to fetch
  everything: the plugin's first collect since the dashboard started, `refresh_seconds`
  since the last full collect that stored, something unnamed moved, or nothing's pending).
  Core asks `changed` as each collect starts, and no longer once more after a refresh,
  ignoring the answer; it asks for a refresh whenever a check says something moved since
  the last, so a check names a subject only when it moved again, and what's pending after a
  collect that fails no longer asks for one at every check. A plugin drops the
  fingerprints, pending sets and full-collect times it kept in `ctx.kv`.
- **One kind of tag**: a tag from `Entity.tags` (a tool's labels) looks like any other, and
  I can take it off (Untag), as I can a `#tag` in an entity's text; either comes back at the
  plugin's next refresh if the tool or the text still has it. One only labels give is still
  named only with its `#`. A plugin that reads `tag` entities sees one only while it's on.
  Every tag's badge looks alike, so `Badge`'s `label` tone, which only core used, is gone.
- **The previous graph for a change check**: `ChangedRequest` gains `previous_graph`, the
  graph the plugin's last change check was given (unless it raised), or None at the first
  since the server started. A plugin that `watches` another compares it with `graph` to see
  what that one's refresh moved, rather than keeping a digest of its entities in `ctx.kv`.
- **Reporting a problem**: `ctx.problem(key, text)` reports something wrong that isn't the
  plugin's refresh failing (a lookup its token isn't allowed, a notifications poll refused,
  two clones of one repo) as its problem `key`, and `ctx.problem(key, None)` says it's gone.
  Core keeps it across restarts, shows it with the plugin's state on the page (its group's
  summary, or the footer) and in `mfd dump` (`plugin_problems`), and logs it only when it
  changes, so a plugin can report it on every refresh rather than remembering what it warned
  of. One the plugin hasn't reported for `prune_days` is forgotten in the daily clean-up.

- **No importing**: `mfd import` is gone, and with it `import_dir`, `ImportRequest`, the
  markdown helpers `api.cell` and `api.tables`, and `LookupCache.replace_imported`. A plugin
  that read a file someone keeps by hand gives a command or a page to set the same instead.
- **Lookup caches**: `LookupCache` (with the sources `BY_HAND`, `IMPORTED` and `LOOKED_UP`)
  keeps a plugin's cache of looked-up values in its own table: by hand wins over a lookup,
  as does what an earlier version's `mfd import` read (`IMPORTED`), and lookups that found
  nothing are tried again after a cutoff.
- **No collecting in pieces**: `Collected` has left `api`. `collect` returns the snapshot,
  and core calls it once per refresh.
- **Focus mode keeps the item itself**: `kept_item`, `KeptItemRequest`, `ctx.kept(key)`,
  `KEPT` and `ActionResult.params` are gone. Core remembers the item a focus page was on when
  a button there was pressed, and once the button has taken it out of the category shows it as
  the page has it now, found by its main subject (or, gone from the page, as it was, without
  its buttons). A plugin whose action leaves its item on the page changed (a todo ticked off)
  gives it the same main subject it had; core adds nothing to an item that's `done`.
- **Fetching some entities again is a collect**: `refresh_subjects`, `SubjectsRequest`,
  `Subjects` and `MAX_SUBJECTS` are gone. An action's `touched` refs, ↻ and `mfd refresh
  --subject` call the publisher's `collect` with `full` False and their ids as `subjects`; one
  that can fetch just those does (github, by PR URL; git, by a branch's id or a repo's common
  git directory), and one that can't fetches everything, as `full` would. It isn't counted as
  the plugin's refresh. github's change checks name PRs by URL now, as its entities' ids.
- **Collecting after the graph** is `collects_after = True`, and `collect` reads the graph
  (`req.graph`, which every collect gets): `collect_after` and `collects_after_graph` are
  gone. Such a plugin is refreshed on its own schedule, no longer with the plugins it
  `requires`; to refresh soon after they change, it `watches` them.
- **No `ctx.data_dir`**: `Context` loses `data_dir` and `data_root`. A plugin keeps what it
  needs in its tables or `ctx.kv`.
- **No `KindSpec.fetched_attrs`**: a fetched entity's attributes fit its family's fetched
  model, or else its kind's own attributes.
- **Timeouts are core's**: the class variables `collect_timeout_seconds` and
  `subjects_timeout_seconds` are gone. `collect` has five minutes, for every plugin.
- **`KindSpec.linking` is a `bool`**: `linking=True` where it was `linking=None`. A pattern
  for its ids comes only from `linking_patterns`, which by default gives none: a kind that
  gave `linking` a pattern string sets `linking=True` and returns the pattern from
  `linking_patterns` (`{kind: pattern}`), or its ids are never found in text.
- **Two hooks no plugin overrode are gone**: `refresh_seconds(ctx)` (a plugin refreshes on
  its section's `refresh_seconds`) and `token_settings(config)` (a plugin keeping its token's
  place under other names overrides `tokens(config)`, returning `{"": settings}`).
- **`changed` returns a `Changed`**, never a bare bool (see "Core keeps what moved" above).
- **`init_suggested` is a class variable**, False by default: `mfd init` suggests a plugin
  only when it sets `init_suggested = True` (git_stack's says whether git stack is
  installed).
- **An `InitSection`**: `init_questions` returns `InitSection(values, shown)`, and
  `init_example` is one too (no longer commented TOML text). `init_shown(section)` is gone: what
  it gave is the section's `shown`, the example values of optional keys, written commented
  out.
- **`api.http` is `request` and `RequestError`**: `api.http.opener` and
  `api.http.require_https` have left `api`. Send a request with `api.http.request`, which
  checks for HTTPS and refuses redirects itself.
- **The example config is the plugins'**: `mfd init --example` is built from each offered
  plugin's section as `mfd init` writes it, installed plugins' too. A plugin setting
  `init_example_on` has its section on there, with `init_example` uncommented; any other's
  is commented out.
- **Commented defaults come from the model**: `init_template` is gone, and a plugin still
  setting it is refused (`mfd check` says why). `mfd init` writes
  each optional key of `config_model` itself, commented out, with its default (a
  `TokenFields` section's `default_token_env` and `default_keychain_service` for an empty
  `token_env` and `keychain_service`) and its docstring's first sentence as its comment. A
  plugin keeps what the model can't say: `init_tables`, commented tables of example entries
  (`[github.teams]`), whose keys aren't written again (nor the tables, when the section's
  answers have one), and a section's `shown` (see "An `InitSection`" above), an example value
  for a key whose default is None or says less. A field marked
  `json_schema_extra={"tables": True}` is left to `init_tables`.
- **Reading a token**: `ctx.token(account=None, *, secret="token")` is the plugin's token (or
  its account's, as `Plugin.tokens` names it), from its `token_env`, else the secret store;
  with neither, it raises `SourceError` with core's message ("no token: run `mfd token set
  <plugin>`, or set $<token_env>", naming it `secret`, e.g. "password"), which `Graph.fetch`
  passes on. `Context` gains `tokens`, which core fills from `Plugin.tokens`.
- **Token fields**: `api.keychain.TokenFields` is a `Strict` base for a section keeping one
  token, with `token_env` and `keychain_service`, defaulting to the class's
  `default_token_env` and `default_keychain_service`.
- **No `sign_in_pages`**: the `Plugin` attribute is gone, and with it the pages served
  without the dashboard's token. Every plugin page needs the token now; a tool's sign-in
  (OAuth) that sends the browser back to the dashboard isn't supported.
- **No `ctx.dashboard_url` or `ctx.page_url(key)`**: only sign-ins sent a browser to a
  plugin's page by its full URL. A page links to another by its path, `/<plugin>/<key>`.
- **Text as shown**: `api.html.shown(text)` drops what could make text a stranger wrote read
  as something else (bidi controls, the zero-width space and zero-width no-break space, the
  word joiner, the soft hyphen, Hangul fillers, tag characters outside a flag; not ZWJ and
  ZWNJ), turns control characters into spaces, and collapses its whitespace. Core shows
  every item's title, subtitle, details, badges and links' text through it; use it for such
  text on a plugin's own pages.
- **One HTTP request**: `api.http.request(url, *, secret_name, headers, json, form, method,
  timeout)` sends a request carrying a secret (https only, no redirects) with a JSON or form
  body, and returns the answer's body. When there's no answer (no network, a timeout, a cut
  or garbled response) or an error status, it raises `api.http.RequestError`, whose `status`
  is the HTTP status, or None without one.
- **No `organisation` family**: `api.kinds.ORGANISATION` and `Organisation` have left `api`,
  and core no longer ranks or badges ("key account") a person's items by an organisation
  they point at. A plugin publishing organisations declares the family itself
  (`Plugin.families`), as for any family core doesn't model.
- **No `Person.importance`**: a person's items rank by their contact type alone (one I set,
  or a tool's `Person.contact_type`), and the "VIP" badge is gone. A plugin that set it
  publishes the person without it, or the graph leaves them out.
- **No Dismiss**: Snooze has a length, for a while or until it changes, which is what Dismiss
  did. `Item.set_aside` is a `bool` (it was `"snooze"` or `"dismiss"`), and the item it marks
  has Unsnooze. A plugin that read it tests it for truth.
- **A drop-down on a plain button**: `Action.choices` no longer needs an `input`; without one,
  the drop-down sits before the button.
- **Bands say what kind of panel it is**: `Band` is `PLAN`, `ASKED`, `MINE`, `WAITING`,
  `HOUSEKEEPING`, `NEXT` and `PARKED`, no longer a step per tool (`REVIEWS`, `CI`, `MERGE`
  and the rest are gone), and `Panel.priority` is a `Band` (by default `HOUSEKEEPING`), not
  any int: `Band.CI + 5` is refused. Pick the band that says what the panel is; a plugin's
  panels in one band go in the order `panels` lists them, and groups whose most urgent
  panels share a band go by plugin name.
- **An item's tags**: core sets `Item.tags`, the tags whose `#tag` badges the item shows, which
  the page's tag view (`?tag=`) keeps it by; a plugin's are ignored.
- **One events hook**: `graph_events` and `GraphEventsRequest` are gone, and `events` takes
  both inputs. `EventsRequest` has the plugin's snapshot `before` the refresh and `after` it,
  and the graph before it (`previous_graph`, where `graph` was) and now (`graph`). It's asked
  after every refresh of anything, as `graph_events` was, never before the plugin's first
  snapshot. A plugin that had `graph_events` moves it into `events`, reading
  `before`/`after` for `previous_snapshot`/`snapshot`; one reading `req.graph` in `events`
  reads `req.previous_graph`. `Harness.graph_events()` is `Harness.events()`, every event the
  plugin makes since the last `store`.
- **`Item.notify`**: an item sets `notify=Notify(kind, text, thing=None)` to raise one of its
  plugin's `notification_kinds` when it first shows in its panels; core compares each
  refresh's panels with the last's (kept in core's `shown_notices`), so a plugin no longer
  builds the panel as it was to compare. An item without `subjects` must give `thing`.
  `Notify` is new in `api`.

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
