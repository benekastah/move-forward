# move-forward

A local dashboard of everything in flight: my PRs and what they're waiting on,
reviews asked of me, PRs my team should look at, local branches with
unpushed or uncommitted work, git-stack health, todos and merge conditions
from the `move-forward` skill's notes, and my Jira tickets. No AI involved:
everything is worked out from GitHub, git, Jira and the notes.

It runs a small HTTP server on localhost, refreshes in the background, and
renders plain HTML with two small bits of JavaScript: the ping page's copy
button, and a snippet that keeps the open sections in the URL (`?open=…`) so
the page's periodic reload and the buttons' redirects don't collapse them.
All state lives in one SQLite database.

## Setup

Requires Python 3.12+, `gh` logged in, and git. `git stack` is optional (for
the `git_stack` plugin).

```sh
~/src/move-forward/dev-install.sh   # .venv, `mfd` on PATH, git hooks, then `mfd init`
mfd serve --open
```

`dev-install.sh` ends with `mfd init` when there is no config yet: it asks for
each setting on the command line, with a guess from the machine as the default
(the GitHub org of the current repo's remote, the teams `gh` says you're in,
your git email), and writes `~/.config/move-forward/config.toml` with the rest
of the settings as commented defaults. `mfd init --example` writes a commented
example without asking; `--force` replaces an existing file.

The top level of the config says who you are and how the dashboard runs:
`email`, `teams` (the names of the teams you're on or review for), `port`,
`db_path`, `db_backups`, `refresh_seconds` (how often the background loop
refreshes), `refresh_on_load_seconds` (a page load refreshes data older than
this) and `prune_days`. Everything about a tool is
in that tool's plugin section; see [Plugins](#plugins).

Teams are named once, at the top level. A plugin that has its own idea of a
team maps the names in a `teams` table of its section: `[github.teams]` gives
a team's GitHub slug where it isn't the name, `[slack.teams]` the channel its
review pings go to, `[jira.teams]` its id in Jira's team field. A name there
that the top-level `teams` doesn't have is an error.

### Jira

Add a `[jira]` section with `site`; requests log in as the top-level `email`
with an API token. Either export `JIRA_API_TOKEN` (or the variable `token_env`
names) or store a token in the macOS keychain under `keychain_service`:

```sh
security add-generic-password -s move-forward-jira -a me@example.com -w <token>
```

Create the token at https://id.atlassian.com/manage-profile/security/api-tokens.

## Plugins

Each external tool is a plugin, enabled by its section in the config: leave a
section out and the plugin, its panels and its fetching are off. Plugins learn
about each other's data only through a shared graph of entities (PRs,
tickets, branches, ...), listed in [docs/entities.md](docs/entities.md);
[docs/plugins.md](docs/plugins.md) draws who publishes and reads what. A
plugin built on another's entities needs that plugin's section too; the config
fails to load without it, saying which (e.g. "[git_stack] needs [git]").

| Plugin | Section | Fetches | Panels |
| --- | --- | --- | --- |
| `github` | `[github]` | my open, merged and closed PRs, review requests, PRs I reviewed, my teams' PRs (with `gh`) | Reviews for me, Feedback, CI failures, Merge conflicts, Needs a reviewer, Ready to merge, Held, PR housekeeping, Requested from my teams |
| `git` | `[git]` | each repo's worktrees and branches | Local work, Cleanup |
| `git_stack` | `[git_stack]`, needs `[git]` | `git stack info` in each of git's repos | Stacks |
| `jira` | `[jira]` | my open tickets | Jira transitions, What next |
| `slack` | `[slack]` | nothing; reads my PRs from the graph and the reviewer map | Nudge reviewers, plus a Ping link on every item about one of my open PRs and the ping page |
| `notes` | `[notes]`, optional: always on | the skill's notes, from markdown | Todos, On ice, plus an Add todo field on every item about one of my open PRs or a ticket, and Put on ice on every item about a local branch |

`[notes] dir` is where `work-notes.md`, `on-ice.md` and `reviewer-map.md`
live: the move-forward skill's notes, which the dashboard reads on every
refresh and writes todos and icing to (default `~/.config/move-forward/notes`;
any directory works without the skill). The smallest config, `email` alone,
is a todo list.

`[github]` needs `org`. `history_days` says how far back my merged and closed
PRs are fetched. Its conventions that differ between organisations are
off by default: `review_checks` (CI checks that really report review status),
`required_owner_marker` (a CODEOWNERS comment such as `#required`),
`ticket_link_prefix` (the PR body line that links the ticket, e.g. `Link`) and
`placeholder_labels` (template labels to replace). `email_domains` picks
reviewers' emails from their commits (default: your email's domain),
`stale_review_hours` says when a review request can be nudged, and
`reviewer_history_days` how far back commits count for reviewer suggestions.

`[git]` needs `repos`, your main checkouts; their other worktrees are found
with `git worktree list`. `my_branch_prefixes` marks branches as yours (without
it every local branch is), `ignored_worktree_globs` leaves worktrees out, and
`commit_history_days` says how far back your commits count towards the
directories you work in.

`[git_stack]` has no settings: it turns on
[git-stack](https://github.com/benekastah/git-stack) for `[git]`'s repos. `mfd
init` adds it when git-stack is installed; add it by hand to an older config.

`[slack]` turns on review pings. `workspace_url` makes the ping page link to
channels and people, `ping_first` and `ping_follow_up` are the messages, and
`[slack.teams]` the channel each team's pings go to (winning over the reviewer
map). Its page is `/ping?pr=<PR URL>`, and its one action
`/actions/slack/mark_sent`.

`[jira]` needs `site`. `token_env` and `keychain_service` say where its token
is (see [Jira](#jira)). `status_in_progress`, `status_in_review` and
`status_closed` are the workflow's status names, for Jira transitions.
`escalation_label_prefix` (e.g. `escalated-to-`) and `escalation_levels`
(what follows it, lowest first) rank escalated incidents and bugs in What
next, and `incident_types` and `bug_types` are the issue types (lower-case
substrings) that count as each. `[jira.teams]` isn't used yet.

## Commands

Every command takes `-c`/`--config <path>` (default
`~/.config/move-forward/config.toml`) and `-v`/`--verbose` (debug logging),
before the command's name.

| Command | Does |
| --- | --- |
| `mfd serve [--open] [--port N]` | Serve the page, refreshing every `refresh_seconds`. |
| `mfd serve --reload` | Development: also restart when a source file of the dashboard or the config file changes (`--open` isn't repeated on restart). The new code and config are checked first, with `mfd check` in a separate process; if that fails, the error is logged, the running server carries on, and the next change tries again. |
| `mfd check` | Load the config and every plugin, print the enabled plugins, and exit; fails with what's wrong. |
| `mfd refresh` | Refresh every source once, print each source's status, exit. |
| `mfd dump` | Print the dashboard as JSON from what's stored. |
| `mfd init [--example] [--force]` | Ask for each setting and write the config (`--example`: write a commented example instead). |

## Panels

The page has **Act now** (reviews for me, feedback, CI, conflicts) on top,
then a collapsible group per enabled plugin holding the rest of its panels.
Only Act now starts expanded; each panel inside a group collapses too.

- **Reviews for me**: PRs requesting my review as a person, and PRs I reviewed
  that were pushed or re-requested since.
- **Feedback to address**: unresolved threads, changes requested, comments
  since my last push I haven't answered.
- **CI failures**: failing checks and merge-queue evictions in the last 2 days.
  Checks named in `review_checks` (ones that report review status rather
  than CI) never count as failures.
- **Merge conflicts**.
- **Needs a reviewer**: no person requested and no approval. PRs requesting
  one of my `teams` get recent committers to the PR's directories as
  suggestions for whom to assign instead.
- **Nudge reviewers** (slack): PRs in order (green, no conflicts, no open threads) with
  no review activity for `[github] stale_review_hours`. The Ping link on any of my
  PRs opens a page with one message per requested reviewer or team, a link to
  their Slack DM or channel, and "mark as sent", which makes the next ping a
  follow-up. Nudges the skill logged in `work-notes.md` count too.
- **Ready to merge** and **Held by merge conditions** (from the work notes).
- **Local work**: dirty worktrees, unpushed and never-pushed branches, with
  buttons: WIP commit (tracked files only), Push (never forced; sets the
  upstream for a new branch), and notes' Put on ice.
- **Stacks** (git_stack): `git stack info` statuses other than up-to-date, and
  tracked branches with no PR, with a button per repo that runs `git stack
  clean -d local` for its merged branches (not offered while the repo has iced
  stacks, since clean touches every stack).
- **Requested from my teams**: PRs waiting on any of my `teams`, ranked by how
  many files CODEOWNERS gives those teams (required first) and how many are in
  directories I commit to.
- **Todos**: open `Left to do` items from the work notes, easy close-outs
  first, blocked last.
- **Jira transitions**: status changes the PR states suggest.
- **What next**: my tickets, incidents (by escalation label, if configured), then bugs, then
  unblocked work.
- **PR housekeeping**: unknown labels, missing ticket links, drafts that look
  ready.
- **Cleanup**: local branches whose PR merged or whose remote is gone, with a
  Delete button each and Delete all (`git branch -D`; a branch checked out in a
  worktree is refused).
- **On ice**: stacks the skill parked, with Reopen (thaw) and Delete the work
  (deletes the local branches after a confirmation, untracking them from
  git-stack first; remote branches and PRs are untouched). Their PRs and
  branches are left out of the other panels.

## Focus mode

The Focus button in the header opens a list of categories: All, each group
and each panel, with counts. Pick one to see a single item at a time, with
Prev and Next (or the left and right arrow keys). All goes through the panels
in the skill's step order: todos, reviews for me, conflicts, feedback, CI
failures, local work, stacks, PR housekeeping, needs a reviewer, team PRs,
nudges, ready to merge, held, cleanup, Jira transitions, what next, on ice.
Work-note entries are split into their todos, one per page, quick ones first.
Each todo lists its entry's PRs, the ones it mentions (by URL, `#number` or
bare number) first and in bold; merged PRs are purple and struck through,
closed ones gray and struck through (no red/green pair). When every PR a
todo mentions is merged or closed, it's flagged "its PRs are done" and its
Done box is drawn bigger. Every PR of mine, every Jira ticket and every work-notes entry on the page
has an Add todo field; a PR's or ticket's todo goes to the entry covering
it, or creates one. Each todo has a Done checkbox, a Delete button, and a Link field that
appends a Jira key (as its URL) or a PR URL to the todo's line; tickets and
PRs a todo mentions are linked under it. Done moves the line from
`Left to do` to a `Done <date>:` line in `work-notes.md` (the skill's format)
and keeps the ticked todo on the page, so unticking it puts it back. Delete
drops the line without recording it.
Buttons on a focused item come back to the same item afterwards.

## API

Everything the page does goes through plain HTTP on `127.0.0.1:<port>`, so a
script (or Claude) can drive it. `mfd dump` prints the same data as JSON.

Requests must name the server as `127.0.0.1:<port>` or `localhost:<port>` in
their `Host` header, and a POST that carries an `Origin` header must come from
one of those. So other web pages open in the browser can't use the dashboard's
buttons, while scripts like `curl`, which send no `Origin`, work as before.
Anything else gets `403 Forbidden`.

Pages (GET, HTML):

| Path | Shows |
| --- | --- |
| `/` | The dashboard. `?open=act,feedback` sets the open sections; `?notice=…` shows a message. |
| `/focus` | Focus mode's category list. |
| `/focus?cat=<key>&i=<n>` | Item `n` of a category: `all`, a group key (`act` or a plugin's name, e.g. `github`) or a panel key. `&done_entry=…&done_text=…` keeps a just-ticked todo on the page. |
| `/ping?pr=<PR URL>` | slack: the ping page for one of my PRs. |

Plugins can add pages of their own; the [Plugins](#plugins) section lists them.

Forms (POST, `application/x-www-form-urlencoded`). A plugin's buttons post to
`/actions/<plugin>/<action>`; the rest are core's. Each answers `303 See Other`
to `/`, or to the `back` query parameter when it's a local path, with
`notice=<outcome>` added; a failure is `notice=failed: <reason>`, never an
error status):

| Path | Fields | Does |
| --- | --- | --- |
| `/refresh` | | Starts a background refresh. |
| `/actions/notes/todo_add` | `entry`, `text` (or `input`), optional `size` (`quick` or `big`), optional `create=1` and `where` (PR or ticket URL) | Adds a todo under the entry's `Left to do`; with `create`, a missing entry is created first, with `where` as its `Where`. |
| `/actions/notes/todo_done` | `entry`, `text` | Moves the todo to a `Done <date>:` line; redirects with `done_entry`/`done_text`. |
| `/actions/notes/todo_undone` | `entry`, `text` | Moves a done todo back under `Left to do`. |
| `/actions/notes/todo_delete` | `entry`, `text` | Drops the todo without recording it. |
| `/actions/notes/todo_link` | `entry`, `text`, `input` (Jira key or URL) | Appends the ticket's or PR's URL to the todo. |
| `/actions/git/push_branch` | `repo`, `branch` | `git push` (sets the upstream if missing; never forces). |
| `/actions/git/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`. |
| `/actions/notes/ice` | `repo`, `branch` | Puts the branch on ice, with the rest of its stack when git_stack knows it. |
| `/actions/notes/thaw` | `branch` | Takes a stack off ice. |
| `/actions/notes/delete_iced` | `branch` | Deletes an iced stack's local branches and thaws it. |
| `/actions/git/delete_branch` | `repo`, `branch` | `git branch -D`. |
| `/actions/git/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`) | Deletes each; one failure doesn't stop the rest. |
| `/actions/git_stack/stack_clean` | `repo` | `git stack clean -d local`; refused while the repo has iced stacks. |
| `/actions/slack/mark_sent` | `pr_url`, `target` (`user:<login>` or `team:<name>`), `kind` (`first` or `follow-up`), `message` | Records a ping as sent, so the next one to that target is a follow-up. |

`entry` is a work-notes entry's title exactly as its `##` line has it; `text`
is the todo's text without its `[quick]`/`[big]` marker, exactly as the
focus page shows it (and as `mfd dump` lists it under the todos panel's
items' `parts`). `repo` is one of `[git] repos`. For
example:

```sh
curl -s -o /dev/null -w '%{redirect_url}\n' -X POST http://127.0.0.1:8765/actions/notes/todo_done \
  --data-urlencode 'entry=APP-1 Fix the widget' --data-urlencode 'text=reply to the thread'
```

## Daily clean-up

Once a day, after a refresh, work finished more than `prune_days` (90) days
ago is cleared: `Done` lines that old are dropped from the work notes, an
entry goes when every PR it names was merged or closed and every ticket in
its title resolved that long ago and it has no open todos (an entry with
neither is cleared `prune_days` after its `Updated` date), and pings that
old leave the database. PR and ticket dates come from `gh pr view` and
Jira; without Jira, entries with ticket keys are kept.

## How it stores things

`dashboard.db` holds each plugin's latest snapshot as JSON, under the
plugin's name (kept when a refresh fails, with the error alongside), the work notes, iced stacks and
reviewer map imported from the skill's `.md` files on every refresh, emails
looked up from commits, and pings marked as sent. The skill's files stay the
source of truth for the notes. The dashboard writes back to them in the
skill's own format: `work-notes.md` when a todo is added, done, undone,
deleted or linked (and the daily clean-up), `on-ice.md` when a stack is put
on ice, thawed or deleted.

The schema is built by migrations: core and each plugin own a list, applied in
order at start-up and counted in `schema_versions`, so an older database
upgrades in place. Before the first migration that would change an existing
database, it's copied to `backups/dashboard.db.<UTC time>` next to it (with
SQLite's backup API, so the copy is consistent); the newest `db_backups` (10)
copies are kept, and `db_backups = 0` turns this off. To roll back, stop the
server and copy a backup over `dashboard.db`.

## Development

```sh
./check.sh            # ruff format --check, ruff check, basedpyright (strict), pytest
.venv/bin/ruff format # fix formatting
```

`dev-install.sh` points the repo at `git_hooks/`: `pre-commit` formats and
lint-fixes staged Python, `pre-push` runs `check.sh`.
