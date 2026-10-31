# Plugin rearchitecture

Goal: every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph. The move-forward skill's notes move from markdown files into
SQLite, behind a CLI and the HTTP API.

## Plugins

Built in, in the package (`move_forward/plugins/<name>/`):

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

`move_forward/plugin.py`:

```python
class Plugin[C: BaseModel, S: BaseModel](Protocol):
    name: str  # "github"; also the snapshot source and table prefix
    title: str  # "GitHub"; the page group's heading
    config_model: type[C]  # the model for its `[name]` section

    # Storage
    migrations: list[str]  # SQL, applied in order; see Schema
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
    def act_now(self, ctx: Context[C], snapshot: S | None, graph: Graph) -> list[Item]: ...
    def pages(self) -> dict[str, PageHandler]: ...  # e.g. "/ping"

    # Writing
    def actions(self) -> dict[str, ActionHandler]: ...  # posted to /actions/<name>/<action>
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

teams = ["core", "web"]       # the teams I belong to or review for, by name

[github]
org = "acme"
# history_days, review_checks, required_owner_marker, ticket_link_prefix,
# placeholder_labels, email_domains, stale_review_hours, reviewer_history_days

[github.teams]                # a team's slug, where it isn't the team's name
web = "web-reviewers"

[git]
repos = ["~/src/app"]         # main checkouts; worktrees are found with `git worktree list`
# my_branch_prefixes, ignored_worktree_globs, commit_history_days

[slack]
workspace_url = "https://acme.slack.com"
# ping_first, ping_follow_up

[slack.teams]                 # where review pings to a team go
core = "#team-core"

[jira]
site = "https://acme.atlassian.net"
# token_env, keychain_service, status_*, escalation_*, incident_types, bug_types

[jira.teams]                  # a team's id in Jira's team field
core = "12345"

[notes]                       # optional; defaults are fine
```

Teams are named in core and nothing else: each plugin that has its own idea of a team maps
the names to it in a `teams` table of its section, with a default where one makes sense (a
GitHub slug defaults to the name). A name a plugin's `teams` table has but the top-level
`teams` list doesn't is an error, so a typo doesn't silently drop a team. Plugins see only
the names, through `Context`, and their own mapping.

The top-level model holds the core fields plus one optional field per built-in plugin, each
typed with that plugin's `config_model`, so unknown keys are still errors and basedpyright
still checks everything. `mfd init` asks the core questions, then which plugins to set up,
as one list answer checked against the registry ("Plugins to set up (github, git, jira,
slack)", defaulting to all of them; `notes` is always on and not offered), then each chosen
plugin's `init_questions`. Plugins left out get no section, so they stay disabled.

### Worktrees

`repos` lists one main checkout per repository. Each refresh runs `git worktree list` in
it, so worktrees made or removed since (e.g. a second checkout at `~/src/app2`) show up
without a config change. A path in `repos` that turns out to be a worktree of another
listed repo is folded into that repo, so listing both doesn't double up. Actions accept any
worktree of a configured repo, not only the listed path.

## Page layout and ordering

The page has one collapsible group per enabled plugin, holding its panels, below
**Act now**: a single ranked panel that core builds from what each plugin nominates through
its `act_now` hook (see Act now below). Act now is core, not a plugin; each plugin decides
what of its own belongs there.

Order is configured by **drag and drop** on the page:

- Each group summary and each panel summary gets a drag handle. Dragging a group reorders
  groups (Act now stays first); dragging a panel reorders it within its group. A small script (HTML5 drag and drop, about 60 lines) posts the new order to
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

## Act now

The top of the page should answer one question for any set of tools: what should I do
next? Today "Act now" is a fixed list of four GitHub panels. This note proposes making it
generic: every plugin describes its items in a shared vocabulary, and one ranking turns
those descriptions into a short list of the best next actions, whatever the tools.

### What makes something worth doing now

Three kinds of item earn a place, matching how people triage by hand:

- **Quick wins**: done in minutes. Reply to a thread, approve a small PR, tick a todo,
  send a follow-up email.
- **Close-outs**: the last step that finishes something bigger. Merge an approved PR,
  move a ticket to Closed, send the signed contract, a work entry's only open todo.
- **Important work**: matters more than its size. An escalated incident, a reviewer who
  is blocked on you, a deal that closes this week.

On top of those, two adjusters:

- **Someone is waiting.** A review request, a question addressed to me, a customer reply.
  Waiting time makes it more urgent, and blocking a person beats blocking a process.
- **It will go stale or get worse.** A deadline, a PR drifting into conflicts, a meeting
  tomorrow that needs prep, a lead going cold.

And one filter: **it must be doable now.** Something blocked on others, on ice, a draft
I'm not ready to show, or waiting for CI is not an action, however important.

### The shared vocabulary

Each plugin already knows these things about its own items; it only needs to say them in
common terms. `Item` gains an optional `signals` field:

```python
class Signals(Model):
    effort: Literal["minutes", "hour", "day", "unknown"] = "unknown"
    closes: str | None = None
    """What doing it finishes, e.g. "APP-12" or "PR 330"; None if it only moves things along."""
    importance: int = 0
    """0 normal, 1 high, 2 critical. Plugin-specific inputs map onto this (escalation
    level, priority, deal size)."""
    waiting: list[str] = []
    """Who is waiting on me for it: logins, names, "customer"."""
    waiting_since: datetime | None = None
    due: datetime | None = None
    """A deadline, or when it will go stale (a lead's follow-up date, a review SLA)."""
    blocked: bool = False
    """Not doable now: waiting on someone else, on ice, CI pending."""
    verb: str = ""
    """What to do, as a short imperative: "Reply to 2 threads", "Merge", "Send the quote"."""
```

A plugin fills in what it knows and leaves the rest at the defaults. Nothing in it is
specific to software: "effort", "closes", "who's waiting" and "due" mean the same for a
PR, a ticket, an email thread or a sales opportunity.

### Examples

| Plugin | Item | effort | closes | importance | waiting | due | verb |
| --- | --- | --- | --- | --- | --- | --- | --- |
| github | Review request, +12 lines | minutes | | 0 | author | request age + SLA | Review |
| github | My PR approved, green | minutes | the PR (and its ticket, via the graph) | 0 | | | Merge |
| github | 2 unresolved threads on my PR | minutes | | 0 | reviewers | | Reply to 2 threads |
| github | CI failing on my PR | hour | | 0 | | | Fix CI |
| jira | Ticket In Review, all PRs merged | minutes | the ticket | 0 | | | Move to Closed |
| jira | Escalated incident | unknown | | 2 | reporter | | Look at the incident |
| notes | Entry's only open todo, `[quick]` | minutes | the entry | 0 | | | the todo's text |
| notes | Todo whose PRs all merged | minutes | | 0 | | | Tick it off |
| git | Branch merged, still local | minutes | | 0 | | | Delete the branch |
| slack | Reviewer quiet for 2 days | minutes | | 0 | | | Bump the review |
| crm (sales) | Opportunity, close date Friday, quote not sent | hour | the opportunity | 1 | buyer | Friday | Send the quote |
| email (sales) | Customer reply unanswered for 26h | minutes | | 1 | customer | reply SLA | Reply |
| calendar | Call tomorrow, no prep notes | hour | | 0 | attendees | the call | Prep for the call |

Effort can come from the tool (story points, a `[quick]` marker, a PR's size), from
simple rules (approve, merge, reply and delete are minutes), or stay unknown.

### Ranking

A score per item, then a diverse top list:

```
score = 3 * closes?                        # finishing beats starting
      + 2 * (effort == minutes) + 1 * (effort == hour)
      + 3 * importance
      + 2 * waiting? + log2(1 + days waiting)
      + due_pressure(due)                  # 0 far away, rising to 4 when overdue
      - 100 * blocked                      # out
```

The weights are a starting point, in config under `[act_now]`, not code:

```toml
[act_now]
size = 7                    # items shown
per_plugin = 3              # so one busy tool can't fill the list
weights = { closes = 3, minutes = 2, hour = 1, importance = 3, waiting = 2, due = 1 }
```

Then:

- **Diversity.** At most `per_plugin` items from one plugin, and at most one per subject:
  if a PR shows up as "Reply to threads" and "Fix CI", the higher one wins and the other
  is mentioned on it ("also: CI failing").
- **Merging across tools through the graph.** "Merge PR 330" and "Move APP-12 to Closed"
  are one close-out when the PR is the ticket's last open one. Act now shows them as one
  item, "Merge PR 330, then close APP-12", with both actions.
- **Explanations.** Every item says why it's there, from the signals that scored: "quick,
  closes APP-12, Alice waiting 2 days". This is what makes the ranking trustworthy and
  tunable.

### Feedback from the person using it

- **Snooze** an item until tomorrow or a date; **dismiss** it for good. Stored in the
  core's `act_now_feedback` table, keyed by the item's subject and verb, so a changed item
  (new commits, a new reply) comes back.
- **Pin** an item to keep it at the top until it's done.
- Optionally later: count which items get acted on from the list versus skipped, and
  nudge the weights. Not needed for a first version; the explanations plus config weights
  get most of the value without any learning.

### Where it lives

In core, not in a plugin. Each plugin decides what of its own it nominates, through one
hook in the plugin protocol:

```python
def act_now(self, ctx: Context[C], snapshot: S | None, graph: Graph) -> list[Item]:
    """This plugin's candidates for Act now, each with `signals` filled in."""
```

The plugin knows its tool best: which of its items are actionable, how long they take,
what they close and how important they are. Core knows nothing about any tool; it only
combines the nominations. Core then:

1. collects every enabled plugin's nominations,
2. drops blocked items and ones the person snoozed or dismissed,
3. merges nominations about the same subject, or about subjects the graph links (a PR and
   its ticket), into one item with all their actions,
4. scores, caps per plugin, and keeps the top `size`,
5. renders the result as the first panel on the page and as focus mode's "Act now".

A nominated item is usually also in one of the plugin's own panels; nominating doesn't move
it. A plugin that implements no `act_now` contributes nothing, and the fallback below lets
it opt in cheaply. Core owns one table for the person's feedback:

```sql
CREATE TABLE act_now_feedback (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    verb TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('snooze', 'dismiss', 'pin')),
    until TEXT,                        -- for snooze
    fingerprint TEXT NOT NULL,         -- the item's state when snoozed or dismissed
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, verb)
);
```

The urgent panels of today (Reviews for me, Feedback, CI failures, Merge conflicts) go back to
their plugins' groups, and the top of the page is this one ranked panel. Focus mode gets
an "Act now" category that walks the same list.

### A version that doesn't depend on every plugin filling in signals

Plugins will fill in signals unevenly, especially third-party ones. A fallback keeps the
panel useful:

- `BasePlugin.act_now` by default nominates every item in the plugin's panels marked
  `actionable` (today's urgent panels), with the panel's default effort and verb for items
  that carry no signals of their own.
- Items in non-actionable panels never appear unless the plugin nominates them itself.
- So a minimal plugin gets into Act now by setting two fields on a panel, and a careful
  plugin overrides `act_now` to nominate precisely and rank better.

### Open questions

- **How much to trust tool-provided importance.** Jira priority is often stale; an
  escalation label is more telling. Per-plugin mapping into 0–2 keeps that judgment in
  the plugin.
- **Effort for unknown items.** Treat unknown as "hour" for scoring, or keep them out of
  the quick-wins slot only? Probably the latter.
- **Time of day.** Quick wins at the end of the day, important work in the morning? An
  easy later addition: a weight profile per part of the day.
- **Teams.** "Someone is waiting" could weigh people on my teams more. That needs the
  `person` entities to know team membership, which github already fetches.

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

Core also owns `act_now_feedback` (see Act now).

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
CREATE TABLE slack_team_channels (     -- the reviewer map's teams; [slack.teams] wins
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
   ping button as an item action. Until step 5 it reads the reviewer map and pings from
   core's tables, which the notes import fills; its own `slack_*` tables arrive with step
   5, when the import goes away.
4. **notes plugin, still on the markdown files**: todos, on ice, add-todo as an item
   action. The page now has a group per plugin plus Act now.
4b. **git-stack plugin**: `[git_stack]` (no keys of its own) runs `git stack info` in each
   repo from git's `repo` entities, in `after_collect`, and owns the Stacks panel, the
   `stack_clean` action and whole-stack icing; it publishes `stack_parent` on `branch`
   entities. Plugins gain `requires` (e.g. `["git"]`), checked when the config loads.
5. **Notes in SQLite**: the notes tables, `mfd notes import/export`, the CLI, todo ids in
   the HTTP API, `notes_dir` removed from the config.
6. **Layout and Act now**: `priority` on panels, the `layout` table, drag and drop,
   `POST /layout`, Reset layout; the `act_now` hook, signals, ranking and the feedback
   table; focus mode follows the layout and gets an Act now category.
7. **Worktrees**: fold a listed worktree into its main checkout; accept any worktree in
   actions.
8. **Init and docs**: choosing plugins in `mfd init`, per-plugin init questions, README sections per plugin,
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

## Follow-ups

Raised while building, not yet scheduled:

- **Which branches are mine.** `my_branch_prefixes` is a hint, not the test. Stronger
  signals, for the git plugin: my commits ahead of the default branch (`git rev-list
  --count --author=<my emails> origin/<default>..<branch>`, limited to recently active
  branches), a PR of mine from the branch in any state (open, draft, merged, closed),
  git-stack tracking it, a reflog that says I created it from a local branch, and being
  checked out in one of my worktrees. All commits ahead by others and no PR of mine is a
  review checkout: not mine, whatever its prefix.
- **Host-qualified repo ids.** `repo` ids are GitHub `owner/name`, from a GitHub-only
  remote regex. A second code host (Bitbucket, GitLab) needs ids like
  `github.com/acme/widgets` so they can't collide, and `git` parsing any host. A shared
  base for code-host plugins (PR panels over a normalized PR) can wait for that second host.
- **Windows.** Once the plugin steps are done, make it run on Windows: the Jira token's
  fallback is the macOS keychain (`security`); `dev-install.sh`, `dev-venv.sh`, `check.sh`
  and the git hooks are shell scripts; `shell` stops subprocesses on ctrl-c with POSIX
  process groups; the reload watcher; repo paths compared as `Path`s (case, `~`,
  separators) and written into `on-ice.md`.
- **CI.** After Windows: GitHub Actions running `check.sh`'s checks, and the tests on
  Linux, macOS and Windows (runners are free for this public repo).
