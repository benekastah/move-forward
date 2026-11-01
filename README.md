# move-forward

A local dashboard of everything in flight: my PRs and what they're waiting on,
reviews asked of me, PRs my team should look at, local branches with
unpushed or uncommitted work, git-stack health, todos and merge conditions
from my work notes, and my Jira tickets. No AI involved:
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
| `slack` | `[slack]` | nothing; reads my PRs from the graph, and where pings go from its own tables | Nudge reviewers, plus a Ping link on every item about one of my open PRs and the ping page |
| `notes` | `[notes]`, optional: always on | nothing: work entries, todos and what's on ice live in the database | Todos, On ice, plus an Add todo field on every item about one of my open PRs or a ticket, and Put on ice on every item about a local branch |

`[notes]` has no settings. The notes (work entries with their todos, and
what's on ice) live in the database. Notes kept as the move-forward skill's
markdown files (`work-notes.md`, `on-ice.md`, `reviewer-map.md`) are read in
once with `mfd notes import <dir>`; `mfd notes export <dir>` writes them back
out. The smallest config, `email` alone, is a todo list.

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
`/actions/slack/mark_sent`. People's Slack ids and teams' channels come from the
skill's `reviewer-map.md`, read with `mfd slack import <dir>` (which also picks
up the nudges the skill logged in `work-notes.md`), or are set by hand on
`/slack/contacts` or with `mfd slack contact|team set`; what's set by hand
wins over a later import.

`[jira]` needs `site`, an `https://` URL (the requests carry your token). `token_env` and `keychain_service` say where its token
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
| `mfd serve [--open] [--port N]` | Serve the page, refreshing every `refresh_seconds`; `--open` opens it signed in, like `mfd open`. |
| `mfd open [--port N]` | Open the running dashboard in a browser, signed in (see [API](#api)). |
| `mfd serve --reload` | Development: also restart when a source file of the dashboard or the config file changes (`--open` isn't repeated on restart). The new code and config are checked first, with `mfd check` in a separate process; if that fails, the error is logged, the running server carries on, and the next change tries again. |
| `mfd check` | Load the config and every plugin, print the enabled plugins, and exit; fails with what's wrong. |
| `mfd refresh` | Refresh every source once, print each source's status, exit. |
| `mfd dump` | Print the dashboard as JSON from what's stored. |
| `mfd notes list [--open]` | The entries with their open todos (`--open`: only those with some). Every `mfd notes` command works on the database without the server, and prints JSON with `--json`, for scripts and the skill. |
| `mfd notes show <entry>` | One entry with its todos, merge conditions and links. An entry is named by its id, its title, or a PR URL or ticket key it's about. |
| `mfd notes add-entry <title> [--link <PR URL or ticket key>]... [--status S] [--where W]` | Add an entry; a ticket key in the title is linked. |
| `mfd notes remove <entry>` | Remove an entry with its todos, for finished work. |
| `mfd notes set <entry> status\|where\|blocked-on <text>` | Set an entry's status, where the work lives (repo, branches, PRs), or what it's blocked on. |
| `mfd notes link <entry> <PR URL or ticket key>` | Say an entry is about a PR or ticket. A key needs a plugin that knows tickets, e.g. `[jira]`. |
| `mfd notes todo add <entry> <text> [--size quick\|big] [--link <PR URL or ticket key>]...` | Add a todo; PR URLs in the text are linked. Prints its id. |
| `mfd notes todo done\|undone\|delete <id>` | Tick a todo off, reopen it, or drop it without recording it as done. |
| `mfd notes todo link <id> <PR URL or ticket key>` | Link a todo to a PR or ticket. |
| `mfd notes condition add\|remove <entry> <text>` | Add or remove a merge condition. |
| `mfd notes decline <entry> <text>` | Record that something offered for an entry (a nudge, say) was turned down today. |
| `mfd notes ice --branch <repo> <branch> [--note N]` | Put a branch, with its git-stack stack, on ice; needs a refresh first, so git knows the branch. |
| `mfd notes ices` | What's on ice, with the ids `thaw` takes. |
| `mfd notes thaw <id>` | Take what's on ice off it. |
| `mfd notes import <dir> [--replace]` | Read the skill's `work-notes.md` and `on-ice.md` from `<dir>` into the database. Lines it doesn't understand are kept with their entry. Refuses to replace notes already imported without `--replace`. |
| `mfd notes export <dir> [--force]` | Write the notes back as `work-notes.md` and `on-ice.md`, in the skill's format, for reading or backup. |
| `mfd github import <dir>` | Read reviewers' emails from the skill's `reviewer-map.md` in `<dir>`, for reviewer suggestions; they win over emails looked up from commits. Run it again whenever the map changes. |
| `mfd slack contacts [--json]` | Where people's and teams' pings go, and whether each was imported or set by hand. |
| `mfd slack contact set <login> <slack id>` / `unset <login>` | Set or forget a person's Slack member id. |
| `mfd slack team set <team> <#channel> [--id C…]` / `unset <team>` | Set or forget a team's channel. |
| `mfd slack import <dir>` | Read people's Slack ids and teams' channels from the skill's `reviewer-map.md` in `<dir>`, and the nudges it logged in `work-notes.md`, replacing what the last import read. |
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
  follow-up. Nudges the skill logged in `work-notes.md` count too, once imported.
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
- **On ice**: parked stacks, with Reopen (thaw). git adds Delete branches
  (the local ones, all or none, after a confirmation; refused for a branch with
  commits since it was iced; remote branches and PRs are untouched), and
  git_stack adds Untrack while git-stack tracks them. The stack stays on ice
  with the commits it was iced at until it's reopened. Their PRs and branches
  are left out of the other panels.

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
Done box is drawn bigger. Every PR of mine, every Jira ticket and every work entry on the page
has an Add todo field; a PR's or ticket's todo goes to the entry covering
it, or creates one. Each todo has a Done checkbox, a Delete button, and a Link field that
links it to a Jira ticket (by key) or a PR (by URL); tickets and PRs a todo
names or is linked to are linked under it. Done records the todo as done
today and keeps it on the page, so unticking it reopens it. Delete drops the
todo without recording it.
Buttons on a focused item come back to the same item afterwards.

## API

Everything the page does goes through plain HTTP on `127.0.0.1:<port>`, so a
script (or Claude) can drive it. `mfd dump` prints the same data as JSON.

Every request but `/login` and the static files must carry the dashboard's
token, so other programs and users on the machine can't read the page or press
its buttons. The token is in `token` next to the database
(`~/.local/share/move-forward/token`), made on first start and readable only by
you. Scripts send it as `Authorization: Bearer <token>`. Browsers get it as a
cookie: `mfd open` visits `/login?token=<token>`, which sets the cookie and
goes to the dashboard. A request without the token gets `401 Unauthorized`;
delete the file to make a new token, which signs every browser out.

Requests must also name the server as `127.0.0.1:<port>` or `localhost:<port>`
in their `Host` header, and a POST that carries an `Origin` header must come
from one of those. So other web pages open in the browser can't use the
dashboard's buttons, while scripts like `curl`, which send no `Origin`, work.
Anything else gets `403 Forbidden`.

Pages (GET, HTML):

| Path | Shows |
| --- | --- |
| `/` | The dashboard. `?open=act,feedback` sets the open sections; `?notice=…` shows a message. |
| `/focus` | Focus mode's category list. |
| `/focus?cat=<key>&i=<n>` | Item `n` of a category: `all`, a group key (`act` or a plugin's name, e.g. `github`) or a panel key. `&kept=<plugin>:<key>` (set by an action's redirect) keeps an item an action just changed, such as a todo just ticked off, on the page so the change can be undone. |
| `/ping?pr=<PR URL>` | slack: the ping page for one of my PRs. |
| `/slack/contacts` | slack: where people's and teams' pings go, imported or set by hand, with a form to change each; the ping page links to it. |

Plugins can add pages of their own; the [Plugins](#plugins) section lists them.

Forms (POST, `application/x-www-form-urlencoded`). A plugin's buttons post to
`/actions/<plugin>/<action>`; the rest are core's. Each answers `303 See Other`
to `/`, or to the `back` query parameter when it's a local path, with
`notice=<outcome>` added; a failure is `notice=failed: <reason>`, never an
error status):

| Path | Fields | Does |
| --- | --- | --- |
| `/refresh` | | Starts a background refresh. |
| `/actions/notes/todo_add` | `entry`, `text` (or `input`), optional `size` (`quick` or `big`), optional `create=1` and `where` (PR or ticket URL) | Adds an open todo at the end of the entry's; with `create`, a missing entry is created first, with `where` as its `Where`. |
| `/actions/notes/todo_done` | `todo` (its id) | Records the todo as done today; redirects with `kept=notes:<id>`, so the focus page keeps it, ticked, to undo. |
| `/actions/notes/todo_undone` | `todo` | Reopens a done todo. |
| `/actions/notes/todo_delete` | `todo` | Drops the todo without recording it. |
| `/actions/notes/todo_link` | `todo`, `input` (Jira key or PR URL) | Links the todo to the ticket or PR. |
| `/actions/git/push_branch` | `repo`, `branch` | `git push` (sets the upstream if missing; never forces). |
| `/actions/git/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`. |
| `/actions/notes/ice` | `repo`, `branch` | Puts the branch on ice, with the rest of its stack when git_stack knows it, recording each branch's commit. |
| `/actions/notes/thaw` | `ice` (its id, as the On ice panel's buttons have it) | Takes a stack off ice. |
| `/actions/git/delete_branch` | `repo`, `branch` | `git branch -D`. Refused for a checked-out branch, or an iced one with commits since it was iced. |
| `/actions/git/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`) | Deletes each; one failure doesn't stop the rest. |
| `/actions/git/delete_together` | `targets` | Deletes all of them or, if any can't go, none. |
| `/actions/git_stack/untrack` | `targets` | `git stack untrack` for each. |
| `/actions/git_stack/stack_clean` | `repo` | `git stack clean -d local`; refused while the repo has iced stacks. |
| `/actions/slack/mark_sent` | `pr_url`, `target` (`user:<login>` or `team:<name>`), `kind` (`first` or `follow-up`), `message` | Records a ping as sent, so the next one to that target is a follow-up. |
| `/actions/slack/set_contact` | `login`, `slack_id` (blank for none) | Sets where a person's pings go, by hand; it wins over imports. |
| `/actions/slack/set_team_channel` | `team`, `channel_name`, `channel_id` (one of them at least) | Sets where a team's pings go, by hand; `[slack.teams]` still wins. |

`entry` is a work entry's title exactly as the page shows it. `todo` is a todo's
id, which `mfd dump` lists in the `fields` of a todo's actions under the todos
panel's items' `parts`. `repo` is one of `[git] repos`. For example:

```sh
curl -s -o /dev/null -w '%{redirect_url}\n' -X POST http://127.0.0.1:8765/actions/notes/todo_done \
  -H "Authorization: Bearer $(cat ~/.local/share/move-forward/token)" \
  --data-urlencode 'todo=12'
```

## Daily clean-up

Once a day, after a refresh, work finished more than `prune_days` (90) days
ago is cleared: todos done that long ago are dropped from the notes, an
entry goes when every PR it names was merged or closed and every ticket in
its title resolved that long ago and it has no open todos (an entry with
neither is cleared `prune_days` after it last changed), and pings that
old leave the database. PR and ticket dates come from `gh pr view` and
Jira; without Jira, entries with ticket keys are kept.

## How it stores things

`dashboard.db` holds each plugin's latest snapshot as JSON, under the
plugin's name (kept when a refresh fails, with the error alongside), the
notes in the notes plugin's tables, where pings go and the pings sent in the
slack plugin's, and reviewers' emails in the github plugin's (from `mfd github
import`, or looked up from commits). It's the
source of truth for the notes; `mfd notes export` writes them out as markdown.
The server's token sits next to it, in `token`.

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
lint-fixes staged Python, `pre-push` runs `check.sh`. GitHub Actions runs
`check.sh` on Linux for every push and pull request
(`.github/workflows/check.yml`).
