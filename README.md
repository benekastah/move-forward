# move-forward

A local dashboard of everything in flight: my PRs and what they're waiting on,
reviews asked of me, PRs my team should look at, local branches with
unpushed or uncommitted work, git stack health, todos and merge conditions
from my work notes, my Jira tickets, mail that needs me, and whom to ping in Slack about my
PRs. No AI involved: everything is worked out from GitHub, git, Jira, mail, Slack and
the notes.

It runs a small HTTP server on localhost, refreshes in the background, and
renders HTML. Browser scripts replace dashboard content after refreshes and
actions while keeping the scroll position, and remember open sections in local storage.
All state lives in one SQLite database.

## Setup

Requires Python 3.12+, `gh` logged in, and git. `git stack` is optional (for
the `git_stack` plugin).

```sh
python3 ~/src/move-forward/dev.py install  # .venv, `mfd` on PATH, git hooks, then `mfd init`
                                # (on Windows: py dev.py install)
mfd serve --open
```

The dashboard listens only on localhost, over plain HTTP. Browsers treat localhost as a
secure context, so nothing it does needs HTTPS.

`dev.py install` ends with `mfd init` when there is no config yet: it asks for
your email, which plugins to set up (github, git, and git_stack when it's
installed, by default; jira, mail and slack when you name them), then each chosen
plugin's settings, with a guess from the machine as the default (the GitHub org
of the current repo's remote, the teams `gh` says you're in, your git email).
Tab completes plugin names, your org's team slugs and repo paths, and the org
question takes the org's URL pasted whole (`https://github.com/acme`).
It writes `~/.config/move-forward/config.toml` with the rest of the settings as
commented defaults, and the plugins you left out as commented-out sections. `mfd init --example` writes the same
for example answers without asking, with mail and jira commented out; `--force` replaces an
existing file.

The top level of the config says who you are and how the dashboard runs:
`email`, `teams` (the names of the teams you're on or review for), `port`,
`db_path`, `db_backups` and `prune_days`. Each plugin that
fetches refreshes on its own schedule, the `refresh_seconds` in its section
(github every 5 minutes, jira and mail every 15, git and git stack every 30 behind
watching their repos, by default), and sooner when
its plugin can tell cheaply that its tool has something new (each plugin's page
says how). Loading the page refreshes nothing: the Refresh buttons (a plugin's, or the
header's) and an item's ↻ do. Everything about a tool is
in that tool's plugin section; see [Plugins](#plugins).

`[people]` says what the kinds of people I deal with mean: `contact_types` (`{ customer = 1,
partner = 1, vendor = 0 }`) are the types the Set type button offers, each with the importance
(0 normal, 1 high, 2 critical) it gives items about such a person. Another type can still be
set; it counts as 0. See [People](#people).

`[act_now]` tunes how Act now ranks what the plugins nominate: `size` (7,
the items shown), `per_plugin` (3, the most one plugin may fill) and `weights`
(what each signal adds to an item's score: `closes = 3`, `minutes = 2`,
`hour = 1`, `waiting = 2` (plus 1 for each doubling of the days they've waited),
`importance = 3` per level (high, critical), and `due = 1` times the due date's
pressure, 0 `due_warning_days` or more ahead and rising to 4 once it's past, and
`blocked = -4` for a blocked item). A blocked item (a ticket with open
blockers, an entry blocked on something) is ranked down, until it has gone
`blocked_stale_days` (3; 0 means never) without a touch: a ticket's last
update or comment, an entry's last edit. Then it's worth chasing, and ranks as
if it weren't blocked.
A due date starts raising an item `due_warning_days` (7) ahead.
A snooze lasts `default_snooze_days` (1: until tomorrow), or `snooze_days[<kind>]`
for a kind of subject (`snooze_days = { pull_request = 7 }` snoozes a PR for a week),
or until the item changes, whichever comes first; with Snooze's "until it changes", only when
it changes (or once it's `prune_days` old). A timed snooze ends at local midnight, or at
`snooze_until_hour` (9: nine in the morning) on its last day.

Teams are named once, at the top level. A plugin that has its own idea of a
team maps the names in a `teams` table of its section: `[github.teams]` gives
a team's GitHub slug where it isn't the name, and `[slack.teams]` the channel
its review pings go to. A name there that the top-level `teams` doesn't have is
an error.

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
| [git_stack](docs/plugins/git_stack.md) | `[git_stack]`, needs `[git]` | stacks from git stack, in git's repos |
| [jira](docs/plugins/jira.md) | `[jira]` | my open tickets |
| [slack](docs/plugins/slack.md) | `[slack]` | review pings: who to ask about my PRs, and the messages |
| [mail](docs/plugins/mail.md) | `[mail]` | unread and flagged mail over IMAP, by subject and sender, the people who matter first |
| [notes](docs/plugins/notes.md) | `[notes]`, optional: always on | the skill's work notes (entries and todos) and what's on ice, in the database |

Plugins from other packages are found by their `move_forward.plugins` entry point: install
one beside the dashboard, e.g. `uv tool install move-forward --with move-forward-linear`,
and give it its section. `mfd check` says when one is left out, and why.

## Commands

Every command takes `-c`/`--config <path>` (default
`~/.config/move-forward/config.toml`) and `-v`/`--verbose` (debug logging),
before the command's name.

| Command | Does |
| --- | --- |
| `mfd serve [--open] [--port N]` | Serve the page, refreshing each plugin on its own `refresh_seconds`, and when it says its tool changed; `--open` opens it signed in, like `mfd open`. The server runs in a child process: `mfd serve` starts a new one when the config file changes, once the new config passes `mfd check` (run in a separate process; if it fails, the error is logged, the running server carries on, and the next change tries again), and when the server fails, unless its port is in use or it keeps failing as it starts. Ctrl-c stops it. |
| `mfd open [--port N]` | Open the running dashboard in a browser, signed in with a one-time code the server gives once it has proved it knows the token (see [API](#api)). |
| `mfd logout` | End every browser session, signing every browser out. |
| `mfd serve --reload` | Development: also restart when a source file of the dashboard changes, not just the config (`--open` isn't repeated on restart). |
| `mfd restart` | Ask a running `mfd serve` supervisor to check the code and config, then replace its server process. This works on macOS, Linux and Windows; on macOS and Linux, `SIGUSR1` to the supervisor process does the same. An already running supervisor must be started once with this version to accept restart requests. |
| `mfd check` | Load the config and every plugin, print the enabled plugins, and exit; fails with what's wrong. |
| `mfd refresh [--subject KIND ID]...` | Refresh every source once, print each source's status, exit. With `--subject` (e.g. `--subject pull_request <URL>`), fetch just those entities again through their plugins, refreshing whole the plugins that can't. |
| `mfd dump` | Print the dashboard as JSON from what's stored. |
| `mfd snooze <entity> [7d\|12h\|changed] [--kind K] [--task T]` | Snooze the items about an entity (named as for `mfd priority`; all its tasks, or `--task`'s), as their Snooze button does: each until it changes or the time is up (default: `[act_now]` `snooze_days` for its kind); with `changed`, only until it changes. It snoozes what has a Snooze button, in Act now or a panel, and says so when nothing does. `mfd unsnooze <entity> [--kind K] [--task T]` brings back anything snoozed (named as for `mfd priority`; all its tasks, or `--task`'s). |
| `mfd priority <entity> <1\|2\|clear> [--kind K]` | Set how much an entity matters: 1 important, 2 critical. The entity is a PR's URL, a ticket's key or an entry's title; a todo or branch needs `--kind notes.todo` or `--kind git.branch` and its id. It replaces the importance a plugin gave the items about it (and counts for the PRs, tickets and entries around it), until the PR merges, the ticket is done or the todo is ticked off. The importance buttons on items do the same; Clear priority does `clear`. |
| `mfd tag <entity> <tag> [--kind K] [--remove]` | Put a `#tag` on an entity (named as for `mfd priority`), or take one I put there off; see [Tags](#tags). |
| `mfd contact <address\|@domain> <type\|clear>` | Set what someone is to me, or everyone at a domain, e.g. `mfd contact @acme.com customer`, as the Set type button does (see [People](#people)). `mfd contacts` prints those I set as JSON (who, contact_type). |
| `mfd snoozes` | Print the Act now items snoozed now, as the page has them (the snooze's time isn't up and the item hasn't changed since), including items on ice, which the page hides, as JSON (kind, id, task, until, fingerprint; `until` is null for a snooze until it changes), for the skill. |
| `mfd import <dir> [--replace]` | Read the move-forward skill's markdown notes (`work-notes.md`, `on-ice.md`, `reviewer-map.md`) from `<dir>`: each enabled plugin imports what it knows of them, saying what, prefixed with its name. Notes already holding notes skip them, saying so, unless `--replace`, which overwrites the notes. One failing (a file it can't read) doesn't stop the others; the command fails then, and when nothing in `<dir>` is imported. See each plugin's page for what it reads. |
| `mfd init [--example] [--force]` | Ask for each setting and write the config (`--example`: write a commented example instead). |
| `mfd token set <plugin>` | Ask for a plugin's API token (jira, slack) or password (mail) without showing it (`<plugin>.<account>` for one of a plugin's accounts, when it reads several), and store it in the system's secret store (the macOS keychain, Windows' Credential Manager, or Linux's desktop keyring through `secret-tool`) under its `keychain_service` (the plugin's, or the account's), with `email` as the account; the config is refused when two tokens are kept under the same one, whatever its case. |
| `mfd token rotate` | Make a new dashboard token (the one in `token` next to the database) and end every session; a running server picks it up by itself, and scripts need the new one. |
| `mfd token check` | Say where each enabled plugin's tokens are found, each account's apart: its environment variable, the secret store, or nowhere. Never prints a token. |

Plugins add commands of their own, `mfd <plugin> ...`: see
[notes](docs/plugins/notes.md#commands), [slack](docs/plugins/slack.md#commands) and
[github](docs/plugins/github.md#commands).

Closing a tab or canceling a page load can disconnect it while the server answers.
`mfd serve` closes that connection without a traceback; `-v` logs the disconnect at
debug level. Other request errors still report their failures.

## Panels

The page has **Act now** on top: the best next actions across every tool, at
most 7, no more than 3 from one plugin, one per PR or ticket. They're shown in a
panel per plugin, so they flow across the page, the panel with the best item
first, and each is badged with its place overall (`#1`, `#2`, ...); focus mode's
Act now goes through them in that order. Each says what to
do and why it's there ("quick, closes APP-12, alice waiting"), and anything
else due on the same subject ("also: Fix CI"), with its buttons. Finishing something counts
most, then quick work, then someone waiting (more the longer they've waited);
important work and a nearing due date add to that ("critical, alice waiting 3d,
due in 2d"); blocked work ranks lower. An item about a unit of work (moving a
ticket, a work entry's todo) joins the best item about something in it, to do
after it, with its buttons: "Reply: PR 330", then "Move to In Review: APP-12". An item made of
several parts, like an entry's todos, lists them with their own buttons. Each
plugin nominates its own items; its page says which. Each item has Snooze
(for a while, by default until tomorrow, or until it changes, picked beside it) and Pin (first
until unpinned), which act on all it shows: what else is due on its subject and what's joined
to it. A snoozed item comes back once it changes (its title, badges or details). Opening an
item's link, or pressing a plugin's button on it, marks it opened: it's dimmed in its place,
with when you opened it, until it changes (or for a day at most). Coming back to the tab
fetches again just what you opened in the last half hour, not its plugins whole; an item's
↻ button does the same for it at once.

Items with something to do have the same Snooze in their own panels. An item snoozed, in Act
now or its panel, leaves Act now and waits in its panel, last and dimmed, with Unsnooze, until
it changes or the snooze ends; panel and group counts leave it out, and focus mode skips it.
Pin is Act now's.

Below Act now is a collapsible group per enabled plugin holding its panels.
Panels go in order of what kind of panel each is (its band): what I
planned (notes' todos), what others ask of me (reviews, replies, mail), my own work that needs
me (failing checks, conflicts, feedback, unpushed commits), what I'm waiting on others for
(reviews asked, merges under way, nudges), housekeeping (PR labels, branches to clean up, ticket
statuses), what to pick up next, and what's on ice. A plugin's panels in one band go in its
own order, and groups by their most urgent panel's band, then by name: notes, github, mail,
git, git_stack, slack, jira.
Drag a group's or a panel's title to move it (a panel within its group; Act
now stays first); the order is kept in the database, and focus mode's list of
panels follows it. A group or panel new since then goes after the one it followed by default.
Panels that order by importance use the priority set on an item's subject first, then
their own rank for ties.
Every item with an entity subject but what's on ice, including a todo in the Notes panel or Focus mode, shows
Important and Critical as one group of buttons (❗️ / ‼️), highlighting the selected level. To set
something aside rather than raise it, Snooze or put it on ice. For an
item about several entities, the choice applies to its first subject. Once one is set, a Clear priority button goes with them. Act now groups
its Snooze, Pin and Put on ice choices too, with emoji icons. Emoji buttons show
their text in a hover title and an accessible name.
Reset layout, in the footer, goes back to the default order.
Only Act now starts expanded; each panel inside a group collapses too. Items
can also nest under related items, with collapsible child lists. The page
remembers which groups, panels and child lists you opened. Each plugin's page
lists its panels.
Dashboard action buttons update the page in place, keeping the scroll position. Without
JavaScript, their normal form redirects still work.

A group's title bar also says when its plugin last refreshed (in local time,
with the date when it wasn't today), "refreshing…" while it does, or "refresh
failed" with the reason on hover. Its Refresh button refreshes just that plugin,
and then the plugins that watch it (git_stack watches git) if what they read
changed; the header's Refresh refreshes them all. The footer lists only plugins without a group.
While any plugin refreshes, the tab icon shows a dotted version of the
dashboard arrow, highlighting one dot at a time. If the browser pauses the
animation in a background tab, it still shows a complete refresh icon. The
normal mark returns when the refresh ends.

## People

The same person can be in several tools. Core treats `person` entities with the same email as
one person, whoever publishes them, so what a CRM says about someone counts for their mail. An
address many people share (`noreply@...`) joins no one.

An item about a person shows their contact type as a badge, and a Set type button that sets it
for that address or for everyone at its domain (not for a domain anyone can have an address at,
like gmail.com). Once one is set, a Clear type button goes with it, asking first; one set on a
domain says so, as it clears everyone there. A type I set beats one a tool gives, and one on an
address beats one on its domain. Their type's importance (`[people] contact_types`)
raises the item in Act now and in panels ordered by importance. It never lowers what a plugin
said, and a priority I set on the item replaces it.

## Tags

A `#tag` in an item's title or description (a PR's, a todo's; not in code or an HTML comment)
puts the tag on it, as does a label its tool gives it (where its plugin says so: a tag only
labels give, often a common word like `bug`, is named only with its `#`), and so does
its Tag field, on any item about something (a mail, a ticket, a branch), or `mfd tag`. Items
show their tags as badges: those put on by hand first, each with an Untag button, then those in
their text, then their tool's labels, coloured apart since only the tool takes them off. One
put on by hand is kept until I take it off, or the item is gone (a deleted entry, a ticked-off todo, a mail once read); it stays
on finished work, a merged PR or a done ticket. Text that names
it, in a title or a description, however its words are spaced, hyphenated or cased ("Garbage day
moved" names `#garbage-day`), points at it. A tag's words are separated by `-` or `_`; it needs
a letter (`#7` is a PR's number), and one word shorter than three letters is named only with
its `#`. Tags relate items without making them one piece of work: icing
one, or setting its priority, doesn't touch the others. Core publishes each as a `tag` entity
(see [entities.md](docs/entities.md)); a plugin reads which tags are on, or named by, what it
shows.

The page can show only what carries some tags (the tag view). The drop-down above Act now lists
every tag the page's items carry, with how many things carry it; picking one, or clicking an
item's `#tag` badge, adds it to the selection, and the page then shows only the items carrying
at least one of the tags selected: in Act now, in every panel and in focus mode. An item carries
the tags its badges show. On the page, one with parts (an entry's todos) is kept whole, parts
and all, when it or one of its parts carries one; focus mode, which steps through a panel's parts
one at a time, skips those carrying none (unless their item does). Act now ranks only what
carries one, so it shows the most urgent of those, numbered from #1. Panels and groups left empty
are left off; a plugin whose group is left off says when it last refreshed, or why it failed, in
the footer, with its Refresh button. While tags are selected, groups and panels can't be dragged.
Each selected tag has a link clearing it, beside one
clearing them all, and one that nothing carries any more stays there to clear. The selection is
in the page's address (`/?tag=chores&tag=garbage-day`), so it can be bookmarked, and the page's
reload, focus mode's links and the buttons' redirects keep it.

## Settings

`/settings` (Settings, in the header) shows the config's settings as a form: the top
level's, `[act_now]`'s, `[notifications]`' and each enabled plugin's, each with what it does. Saving writes
only the settings that changed into the config file, each on its own line, keeping the
file's comments and hand edits, and only if the result loads; otherwise the page says why
and nothing is written. `mfd serve` then restarts with it, as it does whenever the file
changes. Tables such as `[github.teams]` are edited in the file, and `db_path` and `port`
aren't changed here, since changing them moves the server. Nor are the settings that
say where a credential goes or comes from, whom mail trusts, or where commands run: `email`,
`[github] host`, `[jira] site`/`token_env`/`keychain_service`, `[mail]`
`host`/`port`/`token_env`/`keychain_service`/`message_url`/`auth_servers`, `[slack]`
`workspace_url`/`token_env`/`keychain_service` and `[git]` `repos`/`github_hosts`.
They're shown read-only; edit the file.

## Notifications

The dashboard can tell you in the browser when something changes: a PR becomes mergeable,
a plugin can't fetch. Nothing is on until you subscribe, by
ticking kinds of events under Notifications on `/settings`, answering `mfd init`'s
question, or listing them in `[notifications] subscribe` (for example
`["dashboard.source_failing"]`). Press "Allow in this browser" on the settings page once.

- Each plugin compares what it fetched, and what the others did, with what they had last
  time (`Plugin.events`), and core compares the plugins' panels with the last refresh's for
  what's newly in them (a branch joining Cleanup), so the first fetch never notifies, and
  only a change does, after a restart too. Events of kinds you haven't subscribed to are never
  stored.
- Events wait on the server until a dashboard tab connects. The server streams them to open
  tabs without a browser timer; older browsers ask every 15 seconds instead. Events from
  while no tab was open are told when you open one. Events older than `keep_hours` (24)
  are dropped.
- Events about the same thing, say one PR's CI failing and a new push, come as one
  notification, even if many events have piled up. A request takes up to 20 different
  things, so repeats about one PR cannot hold up other alerts. Clicking one opens the
  dashboard at the item, which is highlighted.
- `dashboard.source_failing`: a plugin has failed to fetch for `failing_minutes` (15), said
  once until it works again.

## Focus mode

The Focus button in the header opens a list of what to walk through: Act now, and each
panel under its group's title, with counts. Pick one to see a single item at a time, with
Prev and Next (or the left and right arrow keys). Act now goes through its items in their
order overall; a panel, in its own. Work entries are split into their todos
([notes](docs/plugins/notes.md#panels) says how). With tags selected (see [Tags](#tags)), it goes
through only what carries one.
Buttons on a focused item come back to the same item afterwards.

## API

Everything the page does goes through plain HTTP on `127.0.0.1:<port>`, so a
script (or Claude) can drive it. Browsers use `mfd.localhost:<port>` (browsers send every
`*.localhost` name to this machine). `mfd dump` prints the
same data as JSON.

Every request must carry the dashboard's token, so other programs and users on the machine
can't read the page or press its buttons, but `/login` and its handshake
(`/login/challenge`, `/login/code`) and the static files. The
token is in `token` next to the database (`~/.local/share/move-forward/token`), made on first
start and readable only by you. Scripts send it as `Authorization: Bearer <token>`; it never
goes in a URL. Browsers never hold it: `mfd open` signs the browser in, through the
handshake, with a session that lasts 400 days. A request without the token or a session gets
`401 Unauthorized`. `mfd logout` or `mfd token rotate` ends every session. How the handshake
and sessions work is in `src/move_forward/auth.py`'s docstring.

Requests must also name the server as `mfd.localhost:<port>`, `127.0.0.1:<port>` or
`localhost:<port>` in their `Host` header, and a POST that carries an `Origin` header must
come from one of those, so other web pages open in the browser can't use the dashboard's
buttons, while scripts like `curl`, which send no `Origin`, work. Anything else gets
`403 Forbidden`. A script with the token is served at whichever of them it asks; a page asked
for without it at `127.0.0.1` or `localhost` is redirected to `mfd.localhost`, where the
browser's session is.

Pages (GET, HTML):

| Path | Shows |
| --- | --- |
| `/` | The dashboard. `?tag=<tag>`, repeated for several, shows only what carries one of them (see [Tags](#tags)). |
| `/settings` | The config's settings as a form (see [Settings](#settings)). |
| `/focus` | Focus mode's category list. |
| `/focus?cat=<key>&i=<n>` | Item `n` of a category: `act` (Act now) or a panel key (its plugin's name and its own, e.g. `github.conflicts`). `&kept=<plugin>:<key>` (set by an action's redirect) keeps an item an action just changed, such as a todo just ticked off, on the page so the change can be undone. |

Both focus pages take `tag` as `/` does.

Plugins add pages and buttons of their own, listed on their pages (see
[Plugins](#plugins)).

`POST /refresh/subjects` (JSON, `[{"kind": ..., "id": ...}]`, at most 50) asks for those
entities to be fetched again, ahead of any refresh that's due; a plugin that can't fetch
one is refreshed whole. It answers `204 No Content`, or `400` for a body that isn't one.

`POST /layout` (JSON, sent by the page's drag script) stores the page's order:
`{"groups": [group keys], "panels": {group key: [panel keys]}}`. It answers
`204 No Content`, or `400` for a body that isn't one.

`GET /notifications/stream` keeps a signed-in connection open and sends notifications as
server events (`data: <JSON list>`), with a heartbeat every five seconds. `POST
/notifications` returns the same JSON once for clients that cannot use the stream. Both
take the events they deliver from the queue. The page opens the stream in one tab at a time,
so open tabs don't use up the browser's few connections to the dashboard.

A plugin's pages are served at `/<plugin>/<page>` (slack's ping page is `/slack/ping`), and
its own page at `/<plugin>`; core's paths' first segments (`focus`, `settings`, `login`,
`notifications`, `refresh`, `layout`, `actions`, the static files) are reserved.

Forms (POST, `application/x-www-form-urlencoded`). A plugin's buttons post to
`/actions/<plugin>/<action>`; the rest are core's. Each answers `303 See Other`
to `/` (filtered by the `tag` query parameters the form posted to, as the page's forms do), or
to the `back` query parameter when it's a local path, with its outcome
in a short-lived cookie the next page shows once (signed, so no other site or app
on localhost can show a notice of its own); an action that fails says
`failed: <reason>`, not an error status. A malformed request gets one: `400`, or `413` for a body
over 64 KiB:

| Path | Fields | Does |
| --- | --- | --- |
| `/settings` | `<section>:<key>` per setting (`:prune_days`, `github:history_days`, `act_now:weights.closes`); a list one per line; a true-or-false one `on` or `off` | Saves the settings the form changed into the config file (see [Settings](#settings)), and redirects back to `/settings` saying what was saved, or why nothing was. Settings left out of the form are left as they are. |
| `/refresh` | `plugin` (optional) | Starts a background refresh of every plugin, or of `plugin` (and then of those that watch it, if what they read changed); `400` for a plugin that isn't enabled or doesn't collect. |
| `/layout/reset` | | Forgets the dragged order of groups and panels. |
| `/actions/act_now/<snooze\|unsnooze\|pin\|unpin\|opened>` | `kind`, `id` (the item's main subject), `task` (what doing it is, e.g. `review`), `fingerprint`; `also` (optional: what's shown with it, as a JSON list of `[kind, id, task, fingerprint]`); for `snooze`, `choice` (optional: `changed` for until it changes, else empty or left out for `[act_now]` `snooze_days`) | Snoozes an Act now item (and what's shown with it) for `snooze_days` (until tomorrow by default) or until it changes, or pins or unpins it; `unsnooze` brings back what's snoozed; `opened` dims it until it changes, or for a day (never replacing a pin, or a snooze still in force). |
| `/actions/act_now/priority` | `kind`, `id` (the entity), `level` (optional: 1 important, 2 critical) | Sets how much the entity matters, as `mfd priority` does; without `level`, clears it. |
| `/actions/act_now/tag` | `kind`, `id` (the entity), `input` (the tag: `garbage-day`, `#Garbage Day`) | Puts the tag on the entity, as `mfd tag` does. |
| `/actions/act_now/untag` | `kind`, `id`, `tag` (its handle) | Takes off a tag I put on it. |
| `/actions/act_now/contact` | `choice` (an address or `@domain`), `input` (the type) | Sets what someone is to me, as `mfd contact` does. |
| `/actions/act_now/contact_clear` | `who` (an address or `@domain`) | Clears the type I set. |

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
until it's reopened), what the notes remembered (`mfd notes remember`) as happening that long ago is forgotten,
pings that old leave the database, and so do Act now's
snoozes once over, and its other snoozes and pins that old. A priority set on work that's since
finished is cleared, and so is a tag I put on a work entry, todo or mail thread that's gone. Reviewer email and Slack id
lookups that found none that long ago are forgotten, to be tried again. PR and ticket dates come from `gh pr view` and
Jira; without Jira, ticket keys aren't known as such, so they hold no entry.

## How it stores things

`dashboard.db` holds what each plugin last fetched, in that plugin's own
tables (kept when a refresh fails, with the error shown alongside), the
notes in the notes plugin's tables, the page's dragged order (core's `layout`), what you
snoozed or pinned in Act now (core's `act_now_feedback`), the priorities, tags and
contact types you set, the sign-in sessions, the notifications waiting to be told and what
the panels last showed that notifies when new (core's too), where pings go and the pings sent in the slack plugin's, reviewers' emails in the github
plugin's (from `mfd import`, or looked up from commits), and the messages
mail reads in its own. It's the
source of truth for the notes; `mfd notes export` writes them out as markdown.
The server's token sits next to it, in `token`. The
database, its backups and the token are readable by you alone (on Windows, by their access lists).

The schema is built by migrations: core and each plugin own a list, applied in
order at start-up and kept by name in the `migrations` table, so an older database
upgrades in place. Before the first migration that would change an existing
database, it's copied to `backups/dashboard.db.<UTC time>` next to it (with
SQLite's backup API, so the copy is consistent); the newest `db_backups` (10)
copies are kept, and `db_backups = 0` turns this off. To roll back, stop the
server and copy a backup over `dashboard.db`.

## Development

```sh
python3 dev.py check     # ruff format --check, ruff check, basedpyright (strict), pytest -n auto
python3 dev.py static    # just the first three, which CI runs once
python3 dev.py tests     # just the tests, in parallel, which CI runs on each system
python3 dev.py coverage  # the tests under coverage.py: what they miss, and htmlcov/index.html
.venv/bin/ruff format    # fix formatting
```

On Windows, run `dev.py` with `py` (`py dev.py check`). `dev.py install` points the repo at
`git_hooks/`: `pre-commit` formats and lint-fixes staged Python, `pre-push` runs
`dev.py check` on a push to `main`, as a branch has CI. GitHub Actions
(`.github/workflows/check.yml`) runs `dev.py static` once, on Linux, and `dev.py tests` on
Linux, macOS and Windows, for each pull request and push to `main`; a pull request that changes
only Markdown (but `docs/entities.md`, which a test reads) skips them.
