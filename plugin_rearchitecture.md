# Plugin rearchitecture

Goal: every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph. The move-forward skill's notes move from markdown files into
SQLite, behind a CLI and the HTTP API.

## Plugins

Built in, in the package (`move_forward_dashboard/plugins/<name>/`):

| Plugin | Config section | Snapshot | Own tables | Panels |
| --- | --- | --- | --- | --- |
| `github` | `[github]` | open, requested, reviewed, team, merged and closed PRs; derived team scores and reviewer suggestions | `github_people` (login, email, source) | Reviews for me, Feedback, CI failures, Merge conflicts, Needs a reviewer, Ready to merge, Held, PR housekeeping, Requested from my teams |
| `git` | `[git]` | local repos: worktrees, branches, stacks (git-stack, if used), my dirs | none | Local work, Stacks (only with git-stack data), Cleanup |
| `jira` | `[jira]` | my open tickets | none | Jira transitions, What next |
| `slack` | `[slack]` | none | `slack_contacts`, `slack_team_channels`, `slack_pings` | Nudge reviewers; the ping page |
| `notes` | `[notes]`, always on | none | `notes_entries`, `notes_todos`, `notes_fields`, `notes_iced_stacks`, `notes_iced_branches` | Todos, On ice |

A plugin is enabled when its config section exists. `notes` is always enabled (with
defaults), so the smallest setup is a config with just `email` and no sections: a todo
list. git-stack stays an enhancement inside `git`: without `git stack info` data there is
no Stacks panel and no stack-aware icing, and nothing else depends on it.

GitHub stacked PRs (`gh` stacks), once `gh` has a stable command for them, become a second
source of stack data in the `git` plugin's Stacks panel; until then only git-stack is read.

## The plugin protocol

`move_forward_dashboard/plugin.py`:

```python
class Plugin[C: BaseModel, S: BaseModel](Protocol):
    name: str                       # "github"; also the snapshot source and table prefix
    title: str                      # "GitHub"; the page group's heading
    config_model: type[C]           # the model for its `[name]` section

    # Storage
    migrations: list[str]           # SQL, applied in order; see Schema
    snapshot_model: type[S] | None  # None for plugins with nothing to fetch

    # Refresh
    def collect(self, ctx: Context[C]) -> S: ...
    def after_collect(self, ctx: Context[C], graph: Graph) -> None: ...  # optional

    # Reading
    def entities(self, ctx: Context[C], snapshot: S | None) -> list[Entity]: ...
    def url_for(self, ctx: Context[C], kind: str, id: str) -> str | None: ...

    # Page
    def panels(self, ctx: Context[C], snapshot: S | None, graph: Graph) -> list[Panel]: ...
    def item_actions(self, ctx: Context[C], item: Item, graph: Graph) -> list[Action]: ...
    def pages(self) -> dict[str, PageHandler]: ...          # e.g. "/ping"

    # Writing
    def actions(self) -> dict[str, ActionHandler]: ...      # posted to /actions/<name>/<action>
    def cli(self, parser: argparse._SubParsersAction) -> None: ...  # `mfd <name> ...`

    # Set-up and upkeep
    def init_questions(self, ask: Asker) -> dict[str, object] | None: ...  # None: skip
    def prune(self, ctx: Context[C], now: datetime, graph: Graph) -> list[str]: ...
```

`Context[C]` carries the plugin's config section, the top-level settings (`email`, `teams`,
`now`), its repository over its own tables, and a namespaced key/value store. A plugin
never receives another plugin's snapshot, config or tables.

Each method has a no-op default in a `BasePlugin` class, so a plugin implements only what
it uses. The registry is a list in `plugins/__init__.py`; third-party plugins (entry
points) can come later without changing the protocol.

### Refresh order

1. Every enabled plugin's `collect` runs in parallel. Each result is stored as its
   snapshot; a failure stores the error and keeps the last good snapshot, as today.
2. The graph is built from every plugin's `entities` over the stored snapshots and tables.
3. `after_collect` runs for plugins that need others' data: `github` computes CODEOWNERS
   scores and reviewer suggestions from the `repo` entities `git` publishes, and looks up
   reviewers' emails.
4. Once a day, every plugin's `prune` (see Upkeep).

## The entity graph

The only way plugins learn about each other's data.

```python
class Ref(Model):
    kind: str          # "pr", "ticket", "branch", "repo", "entry", "person", ...
    id: str            # a PR URL, a ticket key, "<repo dir>\t<branch>", a login

class Entity(Model):
    ref: Ref
    title: str
    url: str | None
    state: str | None              # "open", "draft", "merged", "closed"; a ticket's status
    refs: list[Ref] = []           # what it points at
    attrs: dict[str, JsonValue] = {}

class Graph:
    def get(self, kind: str, id: str) -> Entity | None
    def of_kind(self, kind: str) -> list[Entity]
    def related(self, ref: Ref, kind: str) -> list[Entity]   # either direction, one hop,
                                                              # plus via a shared ticket
    def url_for(self, kind: str, id: str) -> str | None      # asks every plugin
```

Kinds and attributes are a published contract, documented in `docs/entities.md`:

| Kind | Published by | id | refs | attrs |
| --- | --- | --- | --- | --- |
| `pr` | github | URL | `ticket` keys from branch, title, body; `branch` (repo, head) | `repo`, `head`, `base`, `draft`, `mine`, `requested_users`, `requested_teams`, `review_blockers`, `last_review_activity` |
| `ticket` | jira | key | none | `summary`, `status`, `status_category`, `type` |
| `repo` | git | GitHub `owner/name` | none | `dir`, `default_branch`, `my_dirs` |
| `branch` | git | `<dir>\t<name>` | `repo` | `ahead`, `behind`, `gone`, `checked_out_in` |
| `entry` | notes | entry id | `pr` URLs and `ticket` keys it names | `merge_conditions`, `open_todos` |
| `iced_branch` | notes | branch name | `repo` | `stack` |
| `person` | github, slack | GitHub login | none | `email`, `slack_id` |

What the cross-plugin features become:

| Feature | How |
| --- | --- |
| PR panels skip iced work | github checks `graph.get("iced_branch", pr.head)` |
| Held by merge conditions; "draft with no open todos" | github reads `entry` entities related to the PR (directly or via its ticket) |
| Local work and Cleanup know a branch's PR | git looks up `pr` entities by `repo` and `head` |
| Jira transitions | jira reads the `pr` entities related to each ticket |
| Todo links show PR states; "its PRs are done" | notes reads `pr` entities' `state`; ticket links come from `graph.url_for("ticket", key)` |
| Add todo on PRs and tickets | notes' `item_actions` adds it to any item whose `subject` is a `pr` or `ticket` |
| Ping button and Nudge reviewers | slack reads `pr` entities with `review_blockers` and requested reviewers, and `person` entities for contacts |
| Reviewer suggestions | github maps commit emails to logins through `person` entities |

`Item` gains `subject: Ref | None`, the entity it shows, which is what `item_actions` keys
on. `Action` gains `href` for link buttons such as Ping.

## Config

```toml
email = "jane@acme.com"
# db_path, port, refresh_seconds, refresh_on_load_seconds, prune_days

[teams.core]                  # identity shared by plugins; github and slack read it
slack_channel = "#team-core"

[github]
org = "acme"
# history_days, review_checks, required_owner_marker, ticket_link_prefix,
# placeholder_labels, email_domains, stale_review_hours

[git]
repos = ["~/src/app"]         # main checkouts; worktrees are found with `git worktree list`
# my_branch_prefixes, ignored_worktree_globs, commit_history_days

[slack]
workspace_url = "https://acme.slack.com"
# ping_first, ping_follow_up

[jira]
site = "https://acme.atlassian.net"
# token_env, keychain_service, status_*, escalation_*, incident_types, bug_types

[notes]                       # optional; defaults are fine
```

The top-level model holds the core fields plus one optional field per built-in plugin, each
typed with that plugin's `config_model`, so unknown keys are still errors and basedpyright
still checks everything. `mfd init` asks the core questions, then for each plugin "Use
GitHub? (y/n)" and that plugin's `init_questions`.

### Worktrees

`repos` lists one main checkout per repository. Each refresh runs `git worktree list` in
it, so worktrees made or removed since (e.g. a second checkout at `~/src/app2`) show up
without a config change. A path in `repos` that turns out to be a worktree of another
listed repo is folded into that repo, so listing both doesn't double up. Actions accept any
worktree of a configured repo, not only the listed path.

## Page layout and ordering

The page has one collapsible group per enabled plugin, holding its panels, plus the
cross-plugin **Act now** group at the top, holding panels their plugins mark urgent
(today: Reviews for me, Feedback, CI failures, Merge conflicts).

Order is configured by **drag and drop** on the page:

- Each group summary and each panel summary gets a drag handle. Dragging a group reorders
  groups; dragging a panel reorders it within its group, or moves it into or out of Act
  now. A small script (HTML5 drag and drop, about 60 lines) posts the new order to
  `POST /layout` and the server stores it.
- The stored layout is the order of everything on the page and in focus mode's "All".
  Defaults come from each panel's `priority` (the skill's step order), so a fresh install
  looks like today's page.
- A panel or group the layout doesn't mention (a new plugin, a new panel) goes where its
  default priority puts it. "Reset layout" in the footer clears the stored order.
- Without JavaScript the page still renders in the stored or default order; only
  reordering needs it.

Layout lives in the database, not the config file: it's changed from the page, per
machine, and the config stays hand-editable without the page rewriting it.

## Schema

All tables are created by migrations. Core owns `schema_versions` and applies each
plugin's `migrations` in order at start-up, recording how many ran.

### Core

```sql
CREATE TABLE schema_versions (
    plugin TEXT PRIMARY KEY,           -- "core" or a plugin name
    version INTEGER NOT NULL           -- migrations applied so far
);
CREATE TABLE snapshots (               -- unchanged: one row per plugin snapshot
    source TEXT PRIMARY KEY,           -- plugin name, or "<plugin>.<part>"
    fetched_at TEXT NOT NULL,
    payload TEXT,                      -- the plugin's snapshot model as JSON
    error TEXT
);
CREATE TABLE kv (                      -- replaces meta: namespaced key/value state
    plugin TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (plugin, key)
);
CREATE TABLE layout (                  -- drag-and-drop order
    kind TEXT NOT NULL,                -- "group" or "panel"
    key TEXT NOT NULL,                 -- group key, or "<plugin>.<panel>"
    parent TEXT,                       -- for a panel: its group ("act" or a plugin name)
    position INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
```

Snapshots stay ephemeral: refetched on every refresh, replaced wholesale, no schema beyond
the model's JSON. Durable data lives only in plugin tables.

### notes

```sql
CREATE TABLE notes_entries (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL UNIQUE,        -- "APP-1 Fix the widget", or a PR URL and title
    status TEXT,
    blocked_on TEXT,                   -- NULL or "nothing" means not blocked
    inferred INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    checked_on TEXT,                   -- the skill's check-in dates
    asked_done_on TEXT
);
CREATE TABLE notes_links (             -- what an entry is about: its "Where"
    entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
    kind TEXT NOT NULL,                -- "pr", "ticket", "branch", "repo"
    ref TEXT NOT NULL,                 -- URL, key, "<dir>\t<branch>", path
    PRIMARY KEY (entry_id, kind, ref)
);
CREATE TABLE notes_todos (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    size TEXT CHECK (size IN ('quick', 'big')),
    done_on TEXT,                      -- NULL while open
    created_at TEXT NOT NULL
);
CREATE TABLE notes_todo_links (        -- PRs and tickets one todo is about
    todo_id INTEGER NOT NULL REFERENCES notes_todos ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    PRIMARY KEY (todo_id, kind, ref)
);
CREATE TABLE notes_conditions (        -- merge conditions
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
    pr_url TEXT,                       -- NULL: applies to all the entry's PRs
    text TEXT NOT NULL,
    created_on TEXT NOT NULL
);
CREATE TABLE notes_declined (          -- "don't offer this again today"
    id INTEGER PRIMARY KEY,
    entry_id INTEGER REFERENCES notes_entries ON DELETE CASCADE,
    on_date TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE notes_iced_stacks (
    id INTEGER PRIMARY KEY,
    repo_dir TEXT NOT NULL,
    bottom_branch TEXT NOT NULL,
    iced_on TEXT NOT NULL,
    note TEXT,
    UNIQUE (repo_dir, bottom_branch)
);
CREATE TABLE notes_iced_branches (
    stack_id INTEGER NOT NULL REFERENCES notes_iced_stacks ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    sha TEXT NOT NULL,
    PRIMARY KEY (stack_id, position)
);
```

Todo links replace today's guessing from the todo's text: the Link button and the CLI
write rows here, and the importer fills them from URLs, `#123` and keys found in the text.
The graph's `entry` entity refs are the union of `notes_links` and its todos' links.
"Last asked about iced stacks" goes in `kv` (`notes`, `last_asked_ice`).

### slack

```sql
CREATE TABLE slack_contacts (          -- the reviewer map's users
    login TEXT PRIMARY KEY,
    slack_id TEXT,
    source TEXT NOT NULL               -- "import", "dashboard"
);
CREATE TABLE slack_team_channels (     -- the reviewer map's teams; [teams.x].slack_channel wins
    team TEXT PRIMARY KEY,
    channel_name TEXT,
    channel_id TEXT
);
CREATE TABLE slack_pings (             -- unchanged apart from the name
    id INTEGER PRIMARY KEY,
    pr_url TEXT NOT NULL,
    target TEXT NOT NULL,              -- "user:<login>" or "team:<slug>"
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX slack_pings_pr_url ON slack_pings (pr_url);
```

### github

```sql
CREATE TABLE github_people (           -- commit emails looked up or imported
    login TEXT PRIMARY KEY,
    email TEXT,
    source TEXT NOT NULL               -- "lookup", "import"
);
```

`git` and `jira` have no tables: everything they show is refetched.

## The notes CLI and API

The skill and the page share one repository layer. Every command prints JSON with
`--json`, plain lines otherwise, and works without the server running:

```sh
mfd notes list [--open] [--json]                 # entries with their todos
mfd notes show <entry>                           # entry by id, title, PR URL or ticket key
mfd notes add-entry <title> [--link <url|key>]... [--status S]
mfd notes set <entry> status|blocked-on <text>
mfd notes link <entry> <url|key>
mfd notes todo add <entry> <text> [--size quick|big] [--link <url|key>]...
mfd notes todo done|undone|delete <todo id>
mfd notes todo link <todo id> <url|key>
mfd notes condition add|remove <entry> <text> [--pr <url>]
mfd notes decline <entry> <text>
mfd notes ice <repo> <branch> [--note N]         # whole git-stack stack if it is one
mfd notes thaw <bottom branch>
mfd notes import <dir>                           # one-time: work-notes.md, on-ice.md, reviewer-map.md
mfd notes export <dir>                           # back to markdown, for reading or backup
```

The HTTP actions become `POST /actions/notes/<action>` with the same fields, and todos are
addressed by id instead of by entry title and text. The README's API section lists both.
After this lands, the skill's SKILL.md switches from editing the markdown files to these
commands; that edit happens in the skill's own repository.

## Upkeep

The daily prune becomes per plugin: notes drops done todos, finished entries and stale
declines; slack drops old pings; github drops looked-up emails nobody references. Each
asks the graph whether a PR is merged or a ticket resolved, and `prune_days` stays one
top-level setting.

## Steps

Each step is its own commit (or a few), with `./check.sh` green and the page working:

1. **Core**: `plugin.py` (protocol, `BasePlugin`, `Context`), `graph.py`, migrations
   runner and `schema_versions`/`kv`/`layout`; the current tables become core's first
   migration so existing databases upgrade in place.
2. **github, git, jira plugins**: move their collectors, panels, actions and config
   sections into `plugins/`; panels read the graph instead of `Inputs`. `[git] repos`
   replaces top-level `repos`. Panel tests move with their plugin, with a shared helper
   that builds a graph from fixtures.
3. **slack plugin**: pings, reviewer contacts, the ping page, the Nudge panel and the
   ping button as an item action.
4. **notes plugin, still on the markdown files**: todos, on ice, add-todo as an item
   action. The page now has a group per plugin plus Act now.
5. **Notes in SQLite**: the notes tables, `mfd notes import/export`, the CLI, todo ids in
   the HTTP API, `notes_dir` removed from the config.
6. **Layout**: `priority` on panels, the `layout` table, drag and drop, `POST /layout`,
   Reset layout; focus mode follows the layout.
7. **Worktrees**: fold a listed worktree into its main checkout; accept any worktree in
   actions.
8. **Init and docs**: per-plugin init questions, README sections per plugin,
   `docs/entities.md`, `docs/writing-a-plugin.md`.

## Risks

- **The panel builder is 1,400 lines with cross-cutting helpers.** The split will expose
  hidden couplings; the entity table above is the contract to hold them to, and adding a
  kind or attribute is a deliberate, documented change.
- **Test churn.** About half the tests construct `panels.Inputs`. They move to per-plugin
  fixtures in step 2; the assertions on panel contents stay as they are.
- **Notes migration loses information the parser doesn't model** (free-text lines,
  `Done` history formats). The importer keeps any line it doesn't understand as an
  entry-level note, and `export` lets you compare before deleting the files.
- **The skill and the dashboard drift.** The CLI's JSON output is the contract; the
  skill change lands in the same week as step 5.
