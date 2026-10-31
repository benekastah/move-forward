# Plan

What's left to build in move-forward. Only what's still to do lives here: when a step or
section is done, remove it in the same PR, and delete this file once it's empty. The code,
`AGENTS.md` and `docs/` are the reference for what's built (so far, steps 1–4b of the
plugin rearchitecture: core, the github, git, jira, slack and notes plugins, and
git_stack).

## Goal

Every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph. The move-forward skill's notes move from markdown files into
SQLite, behind a CLI and the HTTP API.

## Remaining steps

Each step is its own stacked branch, with core changes in a branch of their own ahead of
the plugin change that needs them (see `AGENTS.md`), `./check.sh` green and the page working.

### 5a. Notes tables and import

- Core, first: the database opens with `PRAGMA foreign_keys = ON` (the planned tables rely
  on `ON DELETE CASCADE`, which SQLite ignores without it), in WAL mode with a busy
  timeout, since `mfd notes` will write while the server runs, from another process.
- The notes tables (`notes_entries`, `notes_links`, `notes_todos`, `notes_todo_links`,
  `notes_conditions`, `notes_declined`, `notes_ice`, `notes_iced`) as the notes plugin's
  migrations.
- `mfd notes import <dir>` / `export <dir>`; lines the importer doesn't understand are kept
  as entry-level notes.
- `[notes] dir` is dropped; core no longer imports markdown on every refresh, and core's
  `work_items` and `iced_stacks` tables go.
- notes stops running git: today `delete_iced` deletes branches and untracks them from
  git-stack itself, and `ice` reads shas. Deleting branches is git's action and untracking
  is git_stack's; they offer them on the On ice item, by its subjects, as item actions.
  notes only records and thaws.

### 5b. Notes CLI and the skill

- The `mfd notes ...` CLI (list, show, add-entry, set, link, todo add/done/undone/delete/
  link, condition, decline, ice, thaw), JSON with `--json`, working without the server.
- Core, first: an action's result can carry redirect parameters.
- Todos addressed by id in `/actions/notes/...`; core stops knowing `notes/todo_done` by
  name, and `panels.py` stops importing notes' `todo_actions` to build the done todo.
- The move-forward skill's SKILL.md switches from editing the markdown to the CLI, in the
  skill's repository, the same week.
- The skill sends the dashboard's token (`Authorization: Bearer`, from the `token` file)
  wherever it calls the HTTP API.

### 5c. slack and github tables

- slack gets its own tables (`slack_contacts`, `slack_team_channels`, `slack_pings`) and
  github `github_people`, and the reviewer-map import goes. Each plugin's first migration
  copies its rows from core's `reviewer_users`, `team_channels` or `pings` when that table
  exists (see AGENTS.md: plugin migrations don't depend on order). Core drops those
  tables in a later migration, once the copies have shipped; a plugin enabled after that
  starts empty.

### 5d. Prune without the clients

- Core, first: a lookup hook, so a plugin can ask the plugin that owns a kind about an
  entity older than its fetched data (a PR merged months ago, a resolved ticket).
- The daily notes prune moves into notes and asks through that hook, instead of core's
  `prune.py` calling the GitHub and Jira clients.

### 5e. Plugin tables instead of JSON

- Core: `store` and `load` hooks on `Plugin`, with JSON defaults, called by `refresh` and
  `Enabled.snapshot` in place of writing `snapshots.payload` directly.
- One branch per plugin, moving its fetched data into its own tables: github (including
  what was the `github.derived` snapshot), git, git_stack, jira. Each migration copies the
  stored JSON snapshot into the new tables, so the page isn't empty until the next refresh.
- Each plugin's models move from core's `domain.py` into the plugin (`models.py`), next to
  its tables. Core's `domain.py` keeps only `Model`.
- Core: once no plugin uses the JSON defaults, they go, and `snapshots` becomes
  `collections` (status only), with a migration.

### 5f. Plugin-owned, typed entity kinds

- Core: kind models declared by plugins and registered with core, validation when the
  graph is built, and typed reads for consumers (`graph.of_kind(kind, View)`).
- One branch per plugin: declare its kinds, namespaced (`github.pr`, `git.branch`, ...),
  and read others' kinds through views.
- Consumers stop parsing entity ids: today git_stack and notes split `<repo dir>\t<branch>`
  themselves. A kind publishes the parts as attributes (`git.branch` has `repo_dir` and
  `name`), and the id stays opaque.
- git_stack publishes `git_stack.stack_branch` instead of adding attributes to git's
  branches, and the graph stops merging refs from two plugins.
- Linking kinds and mentions (see Units of work): `jira.ticket` and `notes.entry` declare
  themselves linking, github publishes the keys a PR mentions instead of refs to
  `ticket`, and `Graph.related` follows any linking kind instead of `ticket` by name.
- Icing anything, with hiding in core (see Icing anything): `notes.iced` declares that it
  hides what it covers, core leaves those items out of panels and Act now, and the
  consumers' own ice checks go. A notes migration renames the kinds stored in `notes_iced`
  and `notes_links` to their namespaced names.
- Core: `item_actions` is called once per item, and slack rebuilds every ping and notes
  rereads its tables on each call. Plugins get a `prepare_item_actions(ctx, graph)` hook
  that does that work once per page and returns the per-item function.
- `docs/entities.md` generated from the kind models, or checked against them by a test.

### 6. Layout

- `priority` on panels; the `layout` table (already migrated) with drag and drop on group
  and panel summaries, `POST /layout`, and Reset layout in the footer.
- Panel keys namespaced as `<plugin>.<panel>`.
- Core stops knowing panels by name: `ACT_NOW` and `FOCUS_ORDER` in `panels.py` give way to
  each panel's `priority` and the layout.
- Focus mode follows the layout.

### 6b. Act now

- A first version: the `act_now` hook, `Item.signals` with `effort`, `closes`, `waiting`,
  `blocked` and `verb` only, the ranking, diversity per plugin and per subject, and an
  explanation on every item. `importance`, `due` and merging across tools through the
  graph come after.
- `Plugin.act_now` fallback from panels marked `actionable`.
- `[act_now]` (`size`, `per_plugin`, `weights`), in all four places a config key goes.
- The `act_now_feedback` table: snooze, dismiss, pin.
- Focus mode gets an Act now category.

### 7. Worktrees

- A path in `[git] repos` that's a worktree of another listed repo is folded into it.
- Actions accept any worktree of a configured repo, not only the listed path.

### 8. Init and docs

- `mfd init` asks which plugins to set up, as one list answer checked against the registry
  (default all; notes always on), then each chosen plugin's `init_questions`.
- README sections per plugin; `docs/writing-a-plugin.md`.

## Follow-ups

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
- **Signing in on `localhost`.** `mfd open` signs in at `127.0.0.1`, and cookies are per
  host, so the dashboard at `localhost:<port>` answers 401. The server could redirect
  `localhost` to `127.0.0.1`.
- **Config sections from the registry.** `Config` lists a field per built-in plugin, and
  its teams check names slack, github and jira. The check could cover every section with a
  `teams` table instead.
- **CI on macOS and Windows.** After Windows: the tests on macOS and Windows runners too
  (free for this public repo); Linux CI comes in step 4d.
- **Icing and git-stack groups.** Putting a stack on ice walks the stack's parents only;
  git_stack doesn't publish groups, so a group's other stacks aren't iced with it.

## Design for the remaining steps

### Plugins, with their tables and kinds after step 5f

Built in, in the package (`move_forward/plugins/<name>/`):

| Plugin | Config section | Fetches | Fetched tables | Kept tables | Publishes | Panels |
| --- | --- | --- | --- | --- | --- | --- |
| `github` | `[github]` | open, requested, reviewed, team, merged and closed PRs; derived team scores and reviewer suggestions | `github_prs` and its child tables, `github_team_members`, `github_reviewer_suggestions`, `github_team_scores` | `github_people` | `github.pr`, `github.person` | Reviews for me, Feedback, CI failures, Merge conflicts, Needs a reviewer, Ready to merge, Held, PR housekeeping, Requested from my teams |
| `git` | `[git]` | local repos: worktrees, branches, my dirs | `git_repos`, `git_worktrees`, `git_branches`, `git_my_dirs` | none | `git.repo`, `git.branch` | Local work, Cleanup |
| `git_stack` | `[git_stack]`, needs `[git]` | `git stack info` in git's checkouts | `git_stack_branches` | none | `git_stack.stack_branch` | Stacks |
| `jira` | `[jira]` | my open tickets | `jira_issues`, `jira_issue_labels`, `jira_issue_blockers` | none | `jira.ticket` | Jira transitions, What next |
| `slack` | `[slack]` | nothing | none | `slack_contacts`, `slack_team_channels`, `slack_pings` | nothing | Nudge reviewers; the ping page |
| `notes` | `[notes]`, always on | nothing | none | `notes_entries`, `notes_links`, `notes_todos`, `notes_todo_links`, `notes_conditions`, `notes_declined`, `notes_ice`, `notes_iced` | `notes.entry`, `notes.iced` | Todos, On ice |

A plugin is enabled when its config section exists. `notes` is always enabled (with
defaults), so the smallest setup is a config with just `email` and no sections: a todo
list.

GitHub stacked PRs (`gh` stacks), once `gh` has a stable command for them, could be a
second source of stack data next to git-stack; until then only git-stack is read.

### Config

`mfd init` asks the core questions, then which plugins to set up,
as one list answer checked against the registry ("Plugins to set up (github, git, jira,
slack)", defaulting to all of them; `notes` is always on and not offered), then each chosen
plugin's `init_questions`. Plugins left out get no section, so they stay disabled.

#### Worktrees

`repos` lists one main checkout per repository. Each refresh runs `git worktree list` in
it, so worktrees made or removed since (e.g. a second checkout at `~/src/app2`) show up
without a config change. A path in `repos` that turns out to be a worktree of another
listed repo is folded into that repo, so listing both doesn't double up. Actions accept any
worktree of a configured repo, not only the listed path.

### Page layout and ordering

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

### Act now

The top of the page should answer one question for any set of tools: what should I do
next? Today "Act now" is a fixed list of four GitHub panels. This note proposes making it
generic: every plugin describes its items in a shared vocabulary, and one ranking turns
those descriptions into a short list of the best next actions, whatever the tools.

#### What makes something worth doing now

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

#### The shared vocabulary

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

A plugin fills in what it knows and leaves the rest at the defaults. The first version
(step 6b) has only `effort`, `closes`, `waiting`, `blocked` and `verb`; `importance`,
`waiting_since` and `due` come after, once the first version's ranking has been lived with. Nothing in it is
specific to software: "effort", "closes", "who's waiting" and "due" mean the same for a
PR, a ticket, an email thread or a sales opportunity.

#### Examples

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

#### Ranking

A score per item, then a diverse top list:

```
score = 3 * closes?                        # finishing beats starting
      + 2 * (effort == minutes) + 1 * (effort == hour)
      + 3 * importance
      + 2 * waiting? + log2(1 + days waiting)
      + due_pressure(due)                  # 0 far away, rising to 4 when overdue
      - 100 * blocked                      # out
```

The weights are a starting point, in config under `[act_now]`, not code. Like every
config key, they go in the model, the README, `mfd init`'s rendered config and
`mfd init --example`:

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

#### Feedback from the person using it

- **Snooze** an item until tomorrow or a date; **dismiss** it for good. Stored in the
  core's `act_now_feedback` table, keyed by the item's main subject (its namespaced kind
  and id) and verb. A changed item comes back: `Item.fingerprint` is a string the
  plugin sets from what would make the item worth another look (github: the head sha and
  the count of reviews and comments; jira: the status and `updated`), defaulting to a hash
  of the item's title, badges and details. Feedback whose fingerprint no longer matches is
  ignored.
- **Pin** an item to keep it at the top until it's done.
- Optionally later: count which items get acted on from the list versus skipped, and
  nudge the weights. Not needed for a first version; the explanations plus config weights
  get most of the value without any learning.

#### Where it lives

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
    fingerprint TEXT NOT NULL,         -- Item.fingerprint when snoozed or dismissed
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, verb)
);
```

The urgent panels of today (Reviews for me, Feedback, CI failures, Merge conflicts) go back to
their plugins' groups, and the top of the page is this one ranked panel. Focus mode gets
an "Act now" category that walks the same list.

#### A version that doesn't depend on every plugin filling in signals

Plugins will fill in signals unevenly, especially third-party ones. A fallback keeps the
panel useful:

- `Plugin.act_now` by default nominates every item in the plugin's panels marked
  `actionable` (today's urgent panels), with the panel's default effort and verb for items
  that carry no signals of their own.
- Items in non-actionable panels never appear unless the plugin nominates them itself.
- So a minimal plugin gets into Act now by setting two fields on a panel, and a careful
  plugin overrides `act_now` to nominate precisely and rank better.

#### Open questions

- **How much to trust tool-provided importance.** Jira priority is often stale; an
  escalation label is more telling. Per-plugin mapping into 0–2 keeps that judgment in
  the plugin.
- **Effort for unknown items.** Treat unknown as "hour" for scoring, or keep them out of
  the quick-wins slot only? Probably the latter.
- **Time of day.** Quick wins at the end of the day, important work in the morning? An
  easy later addition: a weight profile per part of the day.
- **Teams.** "Someone is waiting" could weigh people on my teams more. That needs the
  `person` entities to know team membership, which github already fetches.

### Data model

#### Where things stand

- **JSON in the database.** Every plugin's snapshot is one JSON blob in `snapshots.payload`
  (`github`, `github.derived`, `git`, `git_stack`, `jira`). So are core's `work_items` and
  `iced_stacks` rows, which step 5a replaces. Nothing can be filtered or indexed in SQL. The
  schema exists only as the pydantic models, and a stored blob that no longer fits its
  model fails when it's read.
- **Untyped entities.** The graph is built in memory on every page load and never stored.
  `Ref.kind` is any string and `Entity.attrs` is a `dict[str, JsonValue]`. Consumers read
  attributes defensively (`str(pr.attrs.get("head"))`), so if a publisher renames an
  attribute, consumers quietly get `"None"`, `False` or `[]`. The contract is only the
  prose in `docs/entities.md`.
- **Shared kinds and core tables.** Two plugins can publish the same ref and the graph
  merges them: git_stack adds `stack_parent` to git's `branch`. Some data still bypasses
  the graph through core's tables: github and slack share core's reviewer table, which
  github writes looked-up emails to (until step 5c splits it into `github_people` and
  `slack_contacts`). Core knows two kinds by name: `ticket` (`Graph.related`) and
  `person` (finding `me`).

#### Principles

1. **No JSON in the database.** Every table has a set schema; a list becomes a child table.
   `kv` holds scalars only.
2. **Plugins own their data.** A plugin's tables hold what it fetches as well as what it
   keeps, prefixed with its name. Core owns only `schema_versions`, collection status,
   `kv`, `layout` and `act_now_feedback`. Tables for PRs, branches, repos, tickets or iced
   branches belong to the plugin that knows them, not to core.
3. **Entity kinds are plugin-owned, one publisher per kind.** The publishing plugin
   declares each of its kinds as a typed model, and kinds are namespaced like panel keys:
   `github.pr`, `git.branch`. Core validates every published entity against its kind's
   model when it builds the graph.
4. **Extend by pointing, not by merging.** A plugin that knows more about another's entity
   publishes its own kind with a ref to it: git_stack publishes `git_stack.stack_branch`
   pointing at the `git.branch`. The graph's merging of one ref from two plugins goes
   away.
5. **Consumers declare what they read.** Plugins still can't import each other, so a
   consumer describes the fields it uses as its own small model, e.g. slack's `PrView`
   with `head`, `draft` and `requested_users`. `graph.of_kind("github.pr", PrView)` returns
   validated instances, and a mismatch raises. If a publisher renames an attribute, that
   consumer's tests fail instead of the page quietly showing less.
6. **The graph stays in memory.** Entities are derived from plugin tables on every build
   and aren't stored. A stored copy would be a second source of truth to keep in step.
7. **The docs follow the models.** `docs/entities.md` is generated from the kind models, or
   a test checks it against them.

#### Units of work: linking kinds

Today `Graph.related` treats `ticket` specially: a PR and a work entry that name APP-1 are
related through it. That only helps people who use Jira, and core knows the kind by name.
The generalization is to let a plugin declare a kind as **linking** on its kind model:

- `jira.ticket` links what mentions its key: PRs by branch, title or body; entries by
  title.
- `notes.entry` links what it lists: its PRs, tickets and branches (from `notes_links`),
  and its todos' links. notes is always on, so every setup has a unit of work, Jira or
  not. An entry can wrap a ticket, or stand alone for work with no ticket.

Mentions need an owner once kinds are namespaced: github shouldn't point at `jira.ticket`
by name, and jira can't see PRs when it publishes. So a publisher records what an entity
mentions as plain text, `mentions` (github: the keys in a PR's branch, title and body), and
a linking kind declares the pattern of its ids (`jira.ticket`: `[A-Z][A-Z0-9]+-\d+`).
Core, building the graph, links an entity to every linking entity whose id it mentions,
and to linking entities that don't exist yet, so `url_for` still makes ticket links
without Jira's data.

`related(ref, kind)` then follows any linking kind, not just `ticket`: entities are related
when one points at the other, or when both are linked by the same ticket or entry. Core
stops knowing `ticket` by name. A future tool's work item (a Linear issue, a GitHub issue)
declares its kind as linking and joins in without core changes.

Work isn't one kind that every plugin must know: it's whatever linking kinds the enabled
plugins publish. Act now merges nominations through them too (a ticket, its PR and its
entry's todo become one item).

#### Icing anything

notes owns icing. It can ice any entity, not just branches:

- **A unit of work** (an entry or a ticket): everything it links is iced with it, including
  PRs and branches that appear later for the same ticket or entry. Todos stay with their
  entry: they're iced with it, never on their own.
- **A PR.**
- **A branch:** with git_stack, its whole stack, as now. Each branch's sha is recorded so
  the stack can be restored after it's deleted.

```sql
CREATE TABLE notes_ice (               -- kept: one act of icing
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,               -- e.g. "APP-12 Fix the widget" or a stack's bottom branch
    iced_on TEXT NOT NULL,
    note TEXT
);
CREATE TABLE notes_iced (              -- kept: what one act of icing covers
    ice_id INTEGER NOT NULL REFERENCES notes_ice ON DELETE CASCADE,
    kind TEXT NOT NULL,                -- e.g. "jira.ticket", "notes.entry", "github.pr", "git.branch"
    ref_id TEXT NOT NULL,
    position INTEGER NOT NULL,         -- a stack's order, bottom first
    sha TEXT,                          -- for branches
    PRIMARY KEY (kind, ref_id)
);
```

notes publishes a `notes.iced` entity per act of icing, pointing at everything it covers.
For an iced entry, that includes what the entry links, which notes knows from its own
tables. An iced ticket reaches its PRs because the ticket is a linking kind.

Plugins don't check for ice themselves. The `notes.iced` kind model declares that it
**hides** what it covers, and core, building the page, leaves out of every panel and of
Act now any item whose subject is hidden: covered directly, or linked to something covered.
The On ice panel is the exception; it shows them. So github, git, git_stack and slack stop
knowing about notes at all, and Act now's `blocked` needn't carry "on ice". This replaces
`notes.iced_branch` and every branch-name lookup.

Put on ice becomes an item action on any item whose subject notes can ice: entries,
tickets, PRs and branches. The On ice panel lists acts of icing, with Thaw, and Delete for
the ones holding branches.

#### Storage in the plugin protocol

`collect` keeps returning the snapshot model S, which panels and `entities` keep using in
memory. Two hooks replace the JSON:

- `store(ctx, snapshot)`: core calls it after a successful `collect`, in one transaction.
  The plugin replaces its fetched tables wholesale. A failed `collect` leaves them alone,
  so the last good data stays, as it does today.
- `load(ctx) -> S | None`: rebuilds S from the plugin's tables, with one query per table
  and the filtering done in SQL.

During the move, the default implementations keep reading and writing JSON. Each plugin
moves on its own branch. Once none uses JSON, the `payload` column goes, and `snapshots`
becomes `collections (source, fetched_at, error)`: status only.

#### Open questions

- **Finding `me`.** `refresh` finds `me` among `person` entities by name. Like linking
  kinds, this could become a declaration on a kind model.
- **A second code host.** With `github.pr` owned by github, a GitLab plugin would publish
  `gitlab.pr`, and every consumer would read both. If that gets repetitive, `pr` could
  become a contract that several plugins publish to. That can wait for the second host.
- **Migration cost.** Every new field GitHub gives us becomes a migration. That's the price
  of a set schema, and it's accepted.

### Schema

All tables are created by migrations. Core owns `schema_versions` and applies each
plugin's `migrations` in order at start-up, recording how many ran. Foreign keys are on
(`PRAGMA foreign_keys = ON`), so the `ON DELETE CASCADE`s below take effect.

Every plugin table is one of two sorts, and its comment in the sketches says which:

- **Fetched** (`-- fetched`): what `collect` brings back, replaced wholesale by `store` on
  every successful collect. Never pruned; a refresh drops what's gone.
- **Kept** (`-- kept`): what only the dashboard knows (notes, pings sent, looked-up
  emails). Survives refreshes, is copied when a migration reshapes it, and is cleared by
  the plugin's `prune`.

#### Core

```sql
CREATE TABLE schema_versions (
    plugin TEXT PRIMARY KEY,           -- "core" or a plugin name
    version INTEGER NOT NULL           -- migrations applied so far
);
CREATE TABLE collections (             -- was snapshots; status only after step 5e
    source TEXT PRIMARY KEY,           -- plugin name, or "<plugin>.<part>"
    fetched_at TEXT NOT NULL,          -- of the last good collect
    error TEXT                         -- the last collect's error, if it failed
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

What a plugin fetches lives in its own tables, replaced wholesale on every successful
collect (see Data model). Core keeps no plugin data.

#### notes

```sql
CREATE TABLE notes_entries (  -- kept
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
CREATE TABLE notes_links (             -- kept: what an entry is about: its "Where"
    entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
    kind TEXT NOT NULL,                -- "pr", "ticket", "branch", "repo"
    ref TEXT NOT NULL,                 -- URL, key, "<dir>\t<branch>", path
    PRIMARY KEY (entry_id, kind, ref)
);
CREATE TABLE notes_todos (  -- kept
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    size TEXT CHECK (size IN ('quick', 'big')),
    done_on TEXT,                      -- NULL while open
    created_at TEXT NOT NULL
);
CREATE TABLE notes_todo_links (        -- kept: PRs and tickets one todo is about
    todo_id INTEGER NOT NULL REFERENCES notes_todos ON DELETE CASCADE,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    PRIMARY KEY (todo_id, kind, ref)
);
CREATE TABLE notes_conditions (        -- kept: merge conditions
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
    pr_url TEXT,                       -- NULL: applies to all the entry's PRs
    text TEXT NOT NULL,
    created_on TEXT NOT NULL
);
CREATE TABLE notes_declined (          -- kept: "don't offer this again today"
    id INTEGER PRIMARY KEY,
    entry_id INTEGER REFERENCES notes_entries ON DELETE CASCADE,
    on_date TEXT NOT NULL,
    text TEXT NOT NULL
);
```

Todo links replace today's guessing from the todo's text: the Link button and the CLI
write rows here, and the importer fills them from URLs, `#123` and keys found in the text.
The graph's `entry` entity refs are the union of `notes_links` and its todos' links.
Icing has its own tables, `notes_ice` and `notes_iced` (see Icing anything).
"Last asked about iced work" goes in `kv` (`notes`, `last_asked_ice`).

#### slack

```sql
CREATE TABLE slack_contacts (          -- kept: the reviewer map's users
    login TEXT PRIMARY KEY,
    slack_id TEXT,
    source TEXT NOT NULL               -- "import", "dashboard"
);
CREATE TABLE slack_team_channels (     -- kept: the reviewer map's teams; [slack.teams] wins
    team TEXT PRIMARY KEY,
    channel_name TEXT,
    channel_id TEXT
);
CREATE TABLE slack_pings (             -- kept: unchanged apart from the name
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

#### github

```sql
CREATE TABLE github_people (           -- kept: commit emails looked up or imported
    login TEXT PRIMARY KEY,
    email TEXT,
    source TEXT NOT NULL               -- "lookup", "import"
);
```

Tables for what github fetches, replacing its `snapshots` JSON:

```sql
CREATE TABLE github_prs (  -- fetched
    url TEXT PRIMARY KEY,
    number INTEGER NOT NULL,
    repo TEXT NOT NULL,                -- owner/name
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL,
    is_draft INTEGER NOT NULL,
    author TEXT,
    head TEXT NOT NULL,
    head_oid TEXT NOT NULL,
    base TEXT NOT NULL,
    default_branch TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    merged_at TEXT,
    last_commit_at TEXT,
    size INTEGER NOT NULL,
    changed_files INTEGER NOT NULL,
    mergeable TEXT NOT NULL,
    merge_state TEXT NOT NULL,
    review_decision TEXT,
    rollup_state TEXT,
    in_merge_queue INTEGER NOT NULL
);
CREATE TABLE github_pr_lists (         -- fetched: why it was fetched; a PR can be in several
    pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,
    list TEXT NOT NULL CHECK (list IN ('mine', 'direct_requests', 'reviewed_by_me',
        'team_requests', 'merged_recent', 'closed_recent')),
    PRIMARY KEY (pr_url, list)
);
CREATE TABLE github_pr_labels (pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,  -- fetched
    label TEXT NOT NULL, PRIMARY KEY (pr_url, label));
CREATE TABLE github_pr_requests (      -- fetched: requested reviewers
    pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('user', 'team')),
    name TEXT NOT NULL,                -- login or team slug
    PRIMARY KEY (pr_url, kind, name)
);
CREATE TABLE github_pr_reviews (pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,  -- fetched
    author TEXT, state TEXT NOT NULL, submitted_at TEXT);
CREATE TABLE github_pr_threads (pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,  -- fetched
    resolved INTEGER NOT NULL, outdated INTEGER NOT NULL, path TEXT, author TEXT,
    body TEXT NOT NULL, url TEXT, created_at TEXT);
CREATE TABLE github_pr_checks (pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,  -- fetched
    name TEXT NOT NULL, state TEXT NOT NULL, url TEXT);
CREATE TABLE github_pr_events (pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,  -- fetched
    type TEXT NOT NULL, at TEXT NOT NULL, actor TEXT,
    reviewer_kind TEXT CHECK (reviewer_kind IN ('user', 'team')), reviewer TEXT, reason TEXT);
CREATE TABLE github_pr_files (pr_url TEXT NOT NULL REFERENCES github_prs ON DELETE CASCADE,  -- fetched
    path TEXT NOT NULL, PRIMARY KEY (pr_url, path));
CREATE TABLE github_team_members (login TEXT PRIMARY KEY);  -- fetched
CREATE TABLE github_reviewer_suggestions (   -- fetched: was the github.derived snapshot
    pr_url TEXT NOT NULL, login TEXT NOT NULL, commits INTEGER NOT NULL,
    PRIMARY KEY (pr_url, login));
CREATE TABLE github_team_scores (  -- fetched
    pr_url TEXT PRIMARY KEY, owned_files INTEGER NOT NULL, required_files INTEGER NOT NULL,
    my_files INTEGER NOT NULL);
```

My login goes in `kv` (`github`, `login`).

#### git

```sql
CREATE TABLE git_repos (  -- fetched
    dir TEXT PRIMARY KEY,              -- the main checkout
    github_repo TEXT,
    default_branch TEXT NOT NULL
);
CREATE TABLE git_worktrees (  -- fetched
    path TEXT PRIMARY KEY,
    repo_dir TEXT NOT NULL REFERENCES git_repos ON DELETE CASCADE,
    branch TEXT,                       -- NULL when detached
    modified INTEGER NOT NULL,
    untracked INTEGER NOT NULL
);
CREATE TABLE git_branches (  -- fetched
    repo_dir TEXT NOT NULL REFERENCES git_repos ON DELETE CASCADE,
    name TEXT NOT NULL,
    upstream TEXT,
    ahead INTEGER NOT NULL,
    behind INTEGER NOT NULL,
    gone INTEGER NOT NULL,
    committed_at TEXT NOT NULL,
    ahead_of_default INTEGER,
    PRIMARY KEY (repo_dir, name)
);
CREATE TABLE git_my_dirs (repo_dir TEXT NOT NULL REFERENCES git_repos ON DELETE CASCADE,  -- fetched
    path TEXT NOT NULL, PRIMARY KEY (repo_dir, path));
```

#### git_stack

```sql
CREATE TABLE git_stack_branches (  -- fetched
    repo_dir TEXT NOT NULL,
    name TEXT NOT NULL,
    parent TEXT,                       -- NULL for a root
    status TEXT,
    current INTEGER NOT NULL,
    stack_group TEXT,
    PRIMARY KEY (repo_dir, name)
);
```

#### jira

```sql
CREATE TABLE jira_issues (  -- fetched
    key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    status_category TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT,
    parent TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    resolved TEXT,
    url TEXT NOT NULL
);
CREATE TABLE jira_issue_labels (key TEXT NOT NULL REFERENCES jira_issues ON DELETE CASCADE,  -- fetched
    label TEXT NOT NULL, PRIMARY KEY (key, label));
CREATE TABLE jira_issue_blockers (key TEXT NOT NULL REFERENCES jira_issues ON DELETE CASCADE,  -- fetched
    blocker TEXT NOT NULL, PRIMARY KEY (key, blocker));
```

### The notes CLI and API

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
mfd notes ice <entry|url|key> [--note N]         # an entry, ticket or PR, with what it links
mfd notes ice --branch <repo> <branch> [--note N] # with its git-stack stack, if it's in one
mfd notes thaw <ice id>
mfd notes import <dir>                           # one-time: work-notes.md, on-ice.md, reviewer-map.md
mfd notes export <dir>                           # back to markdown, for reading or backup
```

The HTTP actions become `POST /actions/notes/<action>` with the same fields, and todos are
addressed by id instead of by entry title and text. The README's API section lists both.
After this lands, the skill's SKILL.md switches from editing the markdown files to these
commands; that edit happens in the skill's own repository.

### Upkeep

The daily prune becomes per plugin: notes drops done todos, finished entries and stale
declines; slack drops old pings; github drops looked-up emails nobody references. Each
asks the graph whether a PR is merged or a ticket resolved, and `prune_days` stays one
top-level setting.

### Risks

- **Notes migration loses information the parser doesn't model** (free-text lines,
  `Done` history formats). The importer keeps any line it doesn't understand as an
  entry-level note, and `export` lets you compare before deleting the files.
- **The skill and the dashboard drift.** The CLI's JSON output is the contract; the
  skill change lands in the same week as step 5b.
