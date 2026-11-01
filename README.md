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
~/src/move-forward/install.sh   # .venv, `mfd` on PATH, git hooks, then `mfd init`
mfd serve --open
```

`install.sh` ends with `mfd init` when there is no config yet: it asks for
your email, which plugins to set up (github, git, and git_stack when it's
installed, by default; jira and slack when you name them), then each chosen
plugin's settings, with a guess from the machine as the default (the GitHub org
of the current repo's remote, the teams `gh` says you're in, your git email).
Tab completes plugin names, your org's team slugs and repo paths, and the org
question takes the org's URL pasted whole (`https://github.com/acme`).
It writes `~/.config/move-forward/config.toml` with the rest of the settings as
commented defaults, and the plugins you left out as commented-out sections. `mfd init --example` writes a commented
example without asking; `--force` replaces an existing file.

The top level of the config says who you are and how the dashboard runs:
`email`, `teams` (the names of the teams you're on or review for), `port`,
`db_path`, `db_backups`, `refresh_seconds` (how often the background loop
refreshes), `refresh_on_load_seconds` (a page load refreshes data older than
this) and `prune_days`. Everything about a tool is
in that tool's plugin section; see [Plugins](#plugins).

`[act_now]` tunes how Act now ranks what the plugins nominate: `size` (7,
the items shown), `per_plugin` (3, the most one plugin may fill) and `weights`
(what each signal adds to an item's score: `closes = 3`, `minutes = 2`,
`hour = 1`, `waiting = 2`).

Teams are named once, at the top level. A plugin that has its own idea of a
team maps the names in a `teams` table of its section: `[github.teams]` gives
a team's GitHub slug where it isn't the name, `[slack.teams]` the channel its
review pings go to, `[jira.teams]` its id in Jira's team field. A name there
that the top-level `teams` doesn't have is an error.

## Plugins

Each external tool is a plugin, enabled by its section in the config: leave a
section out and the plugin, its panels and its fetching are off. Plugins learn
about each other's data only through a shared graph of entities (PRs,
tickets, branches, ...), listed in [docs/entities.md](docs/entities.md);
[docs/graph.md](docs/graph.md) explains how the graph works, and
[docs/plugins.md](docs/plugins.md) draws who publishes and reads what;
[docs/writing-a-plugin.md](docs/writing-a-plugin.md) walks through making one. A
plugin built on another's entities needs that plugin's section too; the config
fails to load without it, saying which (e.g. "[git_stack] needs [git]").

Each plugin's page has its config, setup, panels, commands and buttons:

| Plugin | Section | What it brings |
| --- | --- | --- |
| [github](docs/plugins/github.md) | `[github]` | my PRs, review requests and my teams' PRs (with `gh`) |
| [git](docs/plugins/git.md) | `[git]` | each repo's worktrees and branches |
| [git_stack](docs/plugins/git_stack.md) | `[git_stack]`, needs `[git]` | stacks from git-stack, in git's repos |
| [jira](docs/plugins/jira.md) | `[jira]` | my open tickets |
| [slack](docs/plugins/slack.md) | `[slack]` | review pings: who to ask about my PRs, and the messages |
| [notes](docs/plugins/notes.md) | `[notes]`, optional: always on | the skill's work notes (entries and todos) and what's on ice, in the database |

## Commands

Every command takes `-c`/`--config <path>` (default
`~/.config/move-forward/config.toml`) and `-v`/`--verbose` (debug logging),
before the command's name.

| Command | Does |
| --- | --- |
| `mfd serve [--open] [--port N]` | Serve the page, refreshing every `refresh_seconds`; `--open` opens it signed in, like `mfd open`. It restarts when the config file changes, once the new config passes `mfd check` (run in a separate process); if it fails, the error is logged, the running server carries on, and the next change tries again. |
| `mfd open [--port N]` | Open the running dashboard in a browser, signed in (see [API](#api)). |
| `mfd serve --reload` | Development: also restart when a source file of the dashboard changes, not just the config (`--open` isn't repeated on restart). |
| `mfd check` | Load the config and every plugin, print the enabled plugins, and exit; fails with what's wrong. |
| `mfd refresh` | Refresh every source once, print each source's status, exit. |
| `mfd dump` | Print the dashboard as JSON from what's stored. |
| `mfd init [--example] [--force]` | Ask for each setting and write the config (`--example`: write a commented example instead). |

Plugins add commands of their own, `mfd <plugin> ...`: see
[notes](docs/plugins/notes.md#commands), [slack](docs/plugins/slack.md#commands) and
[github](docs/plugins/github.md#commands).

## Panels

The page has **Act now** on top: the best next actions across every tool, at
most 7, no more than 3 from one plugin, one per PR or ticket. Each says what to
do and why it's there ("quick, closes APP-12, alice waiting"), and anything
else due on the same subject ("also: Fix CI"). Finishing something counts
most, then quick work, then someone waiting; blocked work is left out. Each
plugin nominates its own items; its page says which. Each item has Snooze
(until tomorrow), Dismiss and Pin (first until unpinned); a snoozed or
dismissed item comes back once it changes (its title, badges or details).

Below Act now is a collapsible group per enabled plugin holding its panels.
Panels go in the skill's step order (each panel's priority), and groups by
their most urgent panel: notes (todos), github, git, git_stack, slack, jira.
Drag a group's or a panel's title to move it (a panel within its group; Act
now stays first); the order is kept in the database, and focus mode follows
it. A group or panel new since then goes after the one it followed by default.
Reset layout, in the footer, goes back to the default order.
Only Act now starts expanded; each panel inside a group collapses too. Each
plugin's page lists its panels.

## Focus mode

The Focus button in the header opens a list of categories: All, each group
and each panel, with counts. Pick one to see a single item at a time, with
Prev and Next (or the left and right arrow keys). All goes through the panels
as the page shows them, but Act now, whose items are in the other panels too.
Work entries are split into their todos ([notes](docs/plugins/notes.md#panels) says how).
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
in their `Host` header (a page asked for at `localhost` is sent to the same path at
`127.0.0.1`, where the sign-in cookie is), and a POST that carries an `Origin` header must come
from one of those. So other web pages open in the browser can't use the
dashboard's buttons, while scripts like `curl`, which send no `Origin`, work.
Anything else gets `403 Forbidden`.

Pages (GET, HTML):

| Path | Shows |
| --- | --- |
| `/` | The dashboard. `?open=act,github.feedback` sets the open sections; `?notice=…` shows a message. |
| `/focus` | Focus mode's category list. |
| `/focus?cat=<key>&i=<n>` | Item `n` of a category: `all`, a group key (`act` or a plugin's name, e.g. `github`) or a panel key (its plugin's name and its own, e.g. `github.conflicts`). `&kept=<plugin>:<key>` (set by an action's redirect) keeps an item an action just changed, such as a todo just ticked off, on the page so the change can be undone. |

Plugins add pages and buttons of their own, listed on their pages (see
[Plugins](#plugins)).

`POST /layout` (JSON, sent by the page's drag script) stores the page's order:
`{"groups": [group keys], "panels": {group key: [panel keys]}}`. It answers
`204 No Content`, or `400` for a body that isn't one.

Forms (POST, `application/x-www-form-urlencoded`). A plugin's buttons post to
`/actions/<plugin>/<action>`; the rest are core's. Each answers `303 See Other`
to `/`, or to the `back` query parameter when it's a local path, with
`notice=<outcome>` added; a failure is `notice=failed: <reason>`, never an
error status):

| Path | Fields | Does |
| --- | --- | --- |
| `/refresh` | | Starts a background refresh. |
| `/layout/reset` | | Forgets the dragged order of groups and panels. |
| `/actions/act_now/<snooze\|dismiss\|pin\|unpin>` | `kind`, `id` (the item's main subject), `verb`, `fingerprint` | Snoozes an Act now item until tomorrow, dismisses it, or pins or unpins it. |

For example, ticking off a todo ([notes](docs/plugins/notes.md#buttons)):

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
neither is cleared `prune_days` after it last changed), a PR, ticket or
entry on ice comes off it once its work is over the same way (a stack stays
until it's reopened), pings that old leave the database, and so do Act now's
snoozes once over, and its dismissals and pins that old. PR and ticket dates come from `gh pr view` and
Jira; without Jira, ticket keys aren't known as such, so they hold no entry.

## How it stores things

`dashboard.db` holds what each plugin last fetched, in that plugin's own
tables (kept when a refresh fails, with the error shown alongside), the
notes in the notes plugin's tables, the page's dragged order (core's `layout`), what you snoozed, dismissed or pinned in Act now (core's `act_now_feedback`), where pings go and the pings sent in the
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

`install.sh` points the repo at `git_hooks/`: `pre-commit` formats and
lint-fixes staged Python, `pre-push` runs `check.sh`. GitHub Actions runs
`check.sh` on Linux for every push and pull request
(`.github/workflows/check.yml`).
