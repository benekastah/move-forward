# Plan

What's left to build in move-forward. Only what's still to do lives here: when a step or
section is done, remove it in the same PR, and delete this file once it's empty. The code,
`AGENTS.md` and `docs/` are the reference for what's built (so far, steps 1–5e of the
plugin rearchitecture: core, the github, git, jira, slack and notes plugins, git_stack,
authentication, CI on Linux, the notes in their own tables behind `mfd notes`, and slack's
and github's own tables).

## Goal

Every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph. The move-forward skill's notes move from markdown files into
SQLite, behind a CLI and the HTTP API.

## Remaining steps

Each step is its own stacked branch, with core changes in a branch of their own ahead of
the plugin change that needs them (see `AGENTS.md`), `./check.sh` green and the page working.

### 6. Layout

- `priority` on panels; the `layout` table (already migrated) with drag and drop on group
  and panel summaries, `POST /layout`, and Reset layout in the footer.
- Panel keys namespaced as `<plugin>.<panel>`.

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

- **One unknown key fails jira's whole fetch.** Jira rejects a `key in (...)` search naming
  a ticket that doesn't exist (mistyped, deleted, or in a project I can't see), so the
  prune learns nothing about any ticket that day and keeps every entry naming one. Fetch
  key by key when the batch is refused, or find whether the search API can warn instead.
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
  fallback (and the Slack token's) is the macOS keychain (`security`); `dev-install.sh`, `dev-venv.sh`, `check.sh`
  and the git hooks are shell scripts; `shell` stops subprocesses on ctrl-c with POSIX
  process groups; the reload watcher; repo paths compared as `Path`s (case, `~`,
  separators) and stored in `notes_ice`.
- **Notes are empty until imported.** After upgrading, the Todos and On ice panels are
  empty until `mfd notes import` runs, with nothing on the page saying so. An empty notes
  plugin could say "run `mfd notes import <dir>`" in its panels.
- **Skill stand-ins for missing notes fields.** The move-forward skill keeps some state
  outside the database until the notes can hold it: check-ins (`Checked`, `Asked-done`) are
  `decline` items with those reserved texts; the once-a-day ice question and the per-PR
  "offered" dates live in its gitignored `daily.json`; merge conditions apply to a whole
  entry, so a PR's URL goes in the condition text; `reviewer-map.md` is read only through
  `mfd slack import` and `mfd github import`, so the skill should set Slack ids with `mfd
  slack contact set` rather than editing the map. Give the notes real fields or tables for these, per-PR
  conditions included, and move the skill onto them.
- **Finished entries are never pruned by the dashboard.** Prune deletes only entries
  finished 90 days ago; the skill clears finished ones itself with `mfd notes remove`.
- **Restoring deleted iced branches.** A stack on ice keeps the commit of each branch, but
  once git deletes them there's no button to bring them back. git could add Restore
  branches on the On ice item, recreating each at its commit; and Reopen on a stack whose
  branches are gone could confirm first, since the commits are forgotten with it.
- **Slack lookups that found nobody are never retried.** A reviewer whose email had no
  Slack account when looked up keeps an empty id until set by hand, even after they join.
  Misses could be looked up again after `prune_days`, or slack's prune could clear them.
- **Stale JSON in `snapshots.payload`.** Core no longer writes it, but the last JSON each
  plugin stored stays there, unused once that plugin's migration has copied it. A plugin
  can't clear core's table, so this needs a core migration that runs only after every
  built-in plugin's copy has shipped, or it stays as harmless dead data.
- **Signing in on `localhost`.** `mfd open` signs in at `127.0.0.1`, and cookies are per
  host, so the dashboard at `localhost:<port>` answers 401. The server could redirect
  `localhost` to `127.0.0.1`.
- **Config sections from the registry.** `Config` lists a field per built-in plugin, and
  its teams check names slack, github and jira. The check could cover every section with a
  `teams` table instead.
- **CI on macOS and Windows.** After Windows: the tests on macOS and Windows runners too
  (free for this public repo), next to the Linux job that runs now.
- **Icing and git-stack groups.** Putting a stack on ice walks the stack's parents only;
  git_stack doesn't publish groups, so a group's other stacks aren't iced with it.

## Design for the remaining steps

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
  core's `act_now_feedback` table, keyed by the item's main subject (its kind
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

What's left of it:

- **Units of work in Act now.** Work isn't one kind every plugin must know, it's whatever
  linking kinds the enabled plugins publish (see `docs/graph.md`), so Act now merges
  nominations through them (a ticket, its PR and its entry's todo become one item).
- **Finding `me`.** `refresh` finds `me` among `person` entities by name. Like linking
  kinds, this could become a declaration on a kind model.
- **A second code host.** A GitLab plugin would publish its own kind of PR (one plugin per
  kind), and every consumer would read both. If that gets repetitive, a PR could become a
  contract several plugins publish to. That can wait for the second host.

### Schema

Core's `layout` table is migrated already (for step 6); `act_now_feedback` (see Act now)
comes with step 6b.

### Upkeep

Each plugin prunes its own data daily (notes: old done todos and finished entries; slack:
old pings). Still to add: notes dropping old declines, and github dropping looked-up emails
no PR has asked about in `prune_days`.

### Risks

- **Notes migration loses information the parser doesn't model** (free-text lines,
  `Done` history formats). The importer keeps any line it doesn't understand as an
  entry-level note, and `export` lets you compare before deleting the files.
- **The skill and the dashboard drift.** The CLI's JSON output is the contract the
  move-forward skill is written against; change it deliberately, with the skill.
