# Working on move-forward

Notes for agents (and people) changing this repo. The README says what the
dashboard does; `docs/entities.md` is the contract between plugins,
`docs/graph.md` explains the graph they go through, `docs/plugins.md`
draws how the plugins use each other's entities, and `docs/writing-a-plugin.md`
walks through making one; `docs/review.md` says how PRs and the whole code
are reviewed; `docs/roadmap.md` is what's left to build.

## Shape of the code

- Core (`src/move_forward/`) knows no tool. It loads the config, enables the
  plugins whose section is there (`registry.py`), runs their collectors,
  builds the entity graph (`graph.py`), and renders the page.
- Each tool is a plugin in `src/move_forward/plugins/<name>/`: `config.py`
  (its section's model), `models.py` (its data), `client.py` (talking to the
  tool), a repository module with its tables' migrations and queries,
  `panels.py` (pure functions from its snapshot and the graph to panels),
  `plugin.py` (the `Plugin` subclass), and `actions.py` if it has buttons.
  Core's `domain.py` has only the base `Model` and `KindModel`.
- Plugins import core only from `move_forward.api` (ruff enforces it, and `tests/test_api.py`
  every form of import), and never import each other (`tests/test_api.py` again) or read
  each other's config, snapshots or tables. What one needs
  from another goes through the graph. A plugin that's built on another declares it in
  `requires`.
- A plugin declares the kinds it publishes in `kinds`, named for it (`git.branch`) or for a
  family (below), each with a `KindSpec`: the
  model of its attributes, which the graph checks its entities against, and its flags
  (`linking`, `hides`, `fetchable`, `gone_means_done`, `resolvable`, `tags_from_text`). An entity says
  whether its work is over in `lifecycle` (`open` or `done`), so core never reads a tool's
  states. What more than one tool may represent (a pull request, a ticket, a person) is a
  family (`api.kinds.Family`): core declares those three, and a plugin (or a package several
  share) declares others in `families`. A plugin's kind of a family extends its model, and
  its ids follow the family's rule (a URL; `<host>/<login>` for a person), so they're
  unique whoever publishes them. A consumer reads
  another plugin's entities with `graph.view(kind, View)`, through its own
  small model of the fields it uses, not `attrs.get`. A kind that stands for a
  unit of work (a ticket, a work entry) is declared `linking` (with
  `linking_patterns` giving its id pattern from the plugin's data); an entity
  that may name one publishes the text in `mentions` (with `mentions_hide=False` where someone
  else wrote it, so what it names can't hide it) rather than refs to
  another plugin's kind. Core adds `tag`s (`tags.py`): what carries one points at it, but
  it's no unit of work. A kind whose entities hide what they point at (ice) is
  declared `hides`, and core leaves items about hidden things off the page;
  plugins don't filter them out themselves.
- A plugin keeps what `collect` fetched in its own tables, with `store`, and
  reads it back with `load`; nothing is kept as JSON. Core keeps only when each plugin
  last fetched and last stored, and why it failed; what its change checks last saw and said
  moved, until a collect started after stores (`change_checks.py`); and what else a plugin
  reports wrong with `ctx.problem(key, text | None)` (a lookup its token isn't allowed), shown
  with its state on the page and logged when it changes, rather than each plugin warning in its
  own way.
- Every plugin hook that runs with the plugin's context takes one request object, so a hook
  can gain an input without breaking its overrides. Most take one of five kinds:
  `ContextRequest(ctx)` (`load`, `linking_patterns`); `Request(ctx, graph, snapshot)`
  (`store`, `entities`, `relate`, `panels`, `act_now`, `identity`, `item_actions`,
  `after_collect`, `prune`, `events`); `RefreshRequest(ctx, graph, full, subjects)`
  (`collect`, `changed`); `RefRequest(ctx, kind, ids)` (`fetch`); and `InputRequest(ctx,
  graph)` with a `Page`, a `Form` or the command line's `args` (pages, buttons, `mfd
  <plugin>`). Two keep a request of their own: `url_for` (`UrlRequest`) and `handle_for`
  (`HandleRequest`). `changed` and `events` get what held before as a `Previous` (`req.previous`: when, the fingerprint,
  the snapshot and the graph then). Those called before there's a context take what they need:
  `init_suggested()`, `init_questions(ask, setup)` and `init_shown(section)` (`mfd init`),
  `tokens(config)` (`mfd token`, and loading the config), `cli(parser)`, `pages()` and
  `actions()`.
- Times are kept in UTC and shown in local time, through `localtime`; a day
  recorded as "today" (a todo done, an entry touched) is the local one,
  `ctx.today`.
- Panel builders are pure: no I/O, no clock (use `ctx.now`), no database
  reads beyond what the plugin passes in. Everything slow happens in
  `collect` or `after_collect` (a plugin whose `collect` needs other plugins' entities to
  know what to fetch sets `collects_after`, and collects once they've stored); `changed`, polled
  every `change_check_seconds`, right after each refresh of a plugin it
  `watches` and as each of its own collects starts, is a cheap check of what moved in its
  tool, so the plugin refreshes sooner: a fingerprint, the subjects that moved, or both.
  Core keeps them (`change_checks.py`), never the plugin: it compares each fingerprint with
  the last, keeps the subjects pending until a collect started after they moved stores, owns the
  full-collect timer, and gives `collect` what's pending and whether it's a full one. A
  check compares other plugins' entities with `req.previous.graph`, the graph its last check was
  given, which core keeps too. Notifications come from two places: `events`,
  asked after every refresh with the plugin's snapshot and the graph before and after it, for
  what changed; and an item's `notify`, which core raises when the item is newly in its
  plugin's panels. Rare work (the daily
  prune) can ask the plugins that publish a kind for entities their snapshots no longer
  hold with `graph.fetch`. Buttons, badges, details and links on other plugins' items
  come from `item_actions`, called once per page (where a plugin reads its tables for them),
  which returns the function core calls per item with whose item it is and where it's
  shown. Core adds its own to items, and plugins theirs, but not to a part that's `done` (a
  todo ticked off, kept to untick, finished within its item: only its own buttons act on it):
  the importance choices on
  every item about an entity (but what `hides`), with Clear priority once one's set; Snooze
  (for a while, or until it changes) on every item with a task, wherever it's shown, and Pin in Act now; on an item
  about a person (`people.py`) their contact type, and Set type for a
  person with an address; and on every item about an
  entity (but what `hides`) its `#tag` badges, a Tag field and an Untag per tag
  (`tags.py`), which it keeps in `Item.tags` and the page filters by (`tag_view.py`). A plugin nominates items for core's Act now with `act_now`, each with `Signals` (effort, what it closes,
  who's waiting, and a `task`: the stable id Act now keeps a snooze or pin by); by
  default, the items of its panels marked `act_now`.
- Every request needs the dashboard's token, a plugin's pages (served at
  `/<plugin>/<key>`) included, except signing in (`/login` and its handshake) and the
  static files.

## Rules for changes

- **One kind of change per PR.** Don't change core in the PR that adds or
  changes a plugin. If a plugin needs something new from core (a hook, a
  `Context` field, a graph method), land that first, on its own, with its own
  tests, and stack the plugin PR on it (`git stack branch`, from
  [git stack](https://github.com/benekastah/git-stack/), which keeps stacked branches and
  their PRs in order).
- **Small enough to review well.** One reviewer reads a PR whole, and it reads a small one
  better: split a large change into a stack of PRs that each do one step and pass `dev.py
  check` and CI on their own (a model, then what uses it; a refactor, then the feature on it)
  rather than one PR of everything.
- **Keep the docs up to date as you code**, in the same commit as the change,
  not in a docs pass afterwards:
  - the README for core: its config keys, commands, command-line options,
    HTTP endpoints and the page; its plugins table must match the code.
  - `docs/api-changes.md` for a change to `move_forward.api`; one that could
    break a plugin built against it also raises `API_VERSION`.
  - `CHANGELOG.md`, under Unreleased, for a change someone driving `mfd` from
    outside must act on (the work-notes skill reads it): a command, its options
    or JSON, a config key, an HTTP endpoint's fields, a page link. Say what to do.
  - the plugin's page, `docs/plugins/<name>.md`, for a plugin: its config keys
    and setup, what it publishes, its panels, what it nominates for Act now, its
    buttons, pages and commands.
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
  - `docs/roadmap.md`: remove what the change finishes, whether a step, a bullet or a
    design section, and delete the file once nothing is left.

  Before committing, look for docs the change made untrue: search the docs
  and docstrings for the names you renamed, moved or removed.
- **Every config key appears in two places**: its model (with a docstring), and the README
  (core's) or the plugin's page in `docs/plugins/`. `mfd init` (and `mfd init --example`,
  built the same way) writes each optional key from its model, commented out with its default
  and its docstring's first sentence. A plugin writes by hand, in its `setup.py`, only what
  its model can't say: a required key with an example value in its `init_example`, a table of
  example entries (`[github.teams]`, `[mail.<name>]`) in its `init_tables`, and from its
  `init_shown(section)` an example value for a key whose default says less (or is None),
  where it's right for that section. A key's comment goes above it, after a blank line, wrapped
  to 100 columns (never inside a code span); write `init_tables`' comments the same way.
  Core's `[notifications]` is still written by hand, in `init.py`. Config models reject unknown
  keys; keep it that way. A key that moves says where it went (see
  `MOVED_SETTINGS` in `config.py`), and a removed plugin's section says it's gone
  (`REMOVED_PLUGINS`).
- **Migrations are append-only.** Core and each plugin own a list of named
  `Migration`s (SQL, or a Python function for a data fix), applied in order and
  kept by name in the `migrations` ledger. Never edit, rename or reorder one
  that has been committed to `main`; add a new one, named with the next
  number (`0004_issue_comments`). A plugin's tables, indexes, views and
  triggers are prefixed with its name: core refuses a migration that makes
  another.
  Write a new one against a scratch `db_path`, not the database `mfd serve --reload` runs
  on: it runs the migration as soon as the file is saved, and once it has, renaming it makes
  the server refuse to start.
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
  never touch the network, nor the real database or its token
  (`conftest` fails one that opens them: use `:memory:` or `tmp_path`).
- **No new runtime dependencies.** pydantic and watchfiles (git watching its repos) are
  the only ones; tools are
  reached through their CLIs (`gh`, `git`) or the standard library.
- **Linux, macOS and Windows, with every change.** Each change must keep working
  on all three; CI runs `dev.py tests` on each (and `dev.py static`, the same everywhere, once),
  and they must pass on all of them.
  Use `pathlib` rather than joining paths with `/`, and write paths into TOML or
  URLs with `as_posix()`. Read and write text as UTF-8 (`encoding="utf-8"`, or `"utf-8-sig"`
  with `errors="replace"` for files people write by hand; Windows' default is its code page):
  `tests/test_encoding.py` checks every `read_text`, `write_text`, builtin `open` and
  `fdopen`. Branch on `sys.platform` rather than assuming POSIX
  (modes, process groups, `os.execv`, `time.tzset`), and when a system needs its
  own way (`private.py`'s access lists, `keychain.py`'s stores), give it one
  rather than skipping it there. Dev tooling goes in `dev.py`, not shell scripts.
  A test that can only run on one system says why in its `skipif`, and the other
  systems get a test of their own.
- **Nothing destructive without a confirm.** Buttons that delete or rewrite
  set `confirm`, never force-push, reset or stash.
- **An item offers the same wherever it's shown.** A button an item has in Act now
  it also has in its panel and in focus mode, but for what only concerns Act now's
  ranking (Pin); what can be ticked off is tickable wherever it's listed; and a line
  telling me to do something comes with the button, where the dashboard can do it.
  What doesn't yet is in `docs/roadmap.md`. When adding a button, look at every surface
  the item appears on.
- **Name a tool as its users do.** A plugin's title is its tool's name ("GitHub", "Git", "Git
  Stack"); a command shown to the user is written as it's typed (`git stack restack`); in prose
  it's lower-case words ("a branch git stack tracks"). The plugin's own name stays its config
  section's (`git_stack`).
- **A class's variables come first.** Its class variables (a plugin's `name`,
  `kinds`, `change_check_seconds` and the like) go at the top, then `__init__`, which
  sets every instance variable, then the methods. Don't define a class variable between
  methods, next to the method it's for, or set a new instance variable outside
  `__init__`.
- **Every save must leave working code.** People run `mfd serve --reload`,
  which restarts on each change. Each file you write must be syntactically
  valid, and every import in the package must still resolve: when moving or
  renaming something, update its importers in the same edit, or add the new
  name before removing the old one. The reloader checks with `mfd check`
  before restarting and waits out a failure, but until the code is fixed the
  page runs the old code.

## Before committing

```sh
python3 dev.py check     # ruff format --check, ruff check, basedpyright (strict), pytest -n auto
python3 dev.py coverage  # optional: what the tests don't run
```

It must pass on a PR's last commit: PRs are squash-merged, so that's what reaches `main`.
Commit in small, logical steps with messages that say why, not just what.

## Reviewing

Every PR is reviewed before it merges, by a fresh agent, and the whole code is reviewed
by several after a run of merges: see [docs/review.md](docs/review.md) for how.
