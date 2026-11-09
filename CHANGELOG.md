# Changelog

What changed for anyone driving `mfd` from outside: its commands, their JSON, the config,
the HTTP endpoints and the page's links. Newest first. Each entry says what to do about it.

## Unreleased

### `[slack] team_channel` is gone; teams' channels are set by hand

A team's ping channel now comes only from `[slack.teams]` or from one set by hand
(`/slack/contacts`, `mfd slack team set`), `[slack.teams]` winning. A config that still sets
`team_channel` is refused, saying it's gone: delete the line, and name each team's channel in
`[slack.teams]` (`web = "#team-web"`) or with `mfd slack team set <team> <#channel>`. Channels
an earlier import read are kept, as set by hand. `mfd import` now sets the reviewer map's
teams' channels as if by hand, leaving a team that has one, and names those whose channel
differs from the map's: it no longer replaces them on a second run. In `mfd slack destinations --json`, every team's
`source` is `dashboard`.

### One `mfd import <dir>` replaces the three imports

`mfd notes import`, `mfd github import` and `mfd slack import` are gone. `mfd import <dir>
[--replace]` reads the skill's markdown from `<dir>` into every enabled plugin that knows it,
printing a line per plugin (`notes: imported …`, `github: …`, `slack: …`):

| Was | Now |
| --- | --- |
| `mfd notes import <dir> [--replace]` | `mfd import <dir> [--replace]` |
| `mfd github import <dir>` | `mfd import <dir>` |
| `mfd slack import <dir>` | `mfd import <dir>` |

A directory with neither `work-notes.md` nor `on-ice.md` no longer fails the notes' part: the
command fails only when no plugin imports anything from it, or when one fails (a file it can't
read), the others importing anyway. Notes already holding notes skip them without `--replace`,
printing `notes: already has notes; skipped, pass --replace to overwrite them`, and the
command still exits 0. So to read a changed `reviewer-map.md` again once the notes are in, run
`mfd import <dir>` without `--replace`: `--replace` overwrites the notes too, losing what was
changed on the dashboard since. `mfd github` has no commands left.

### Slack's sign-in is gone

slack no longer connects to a Slack app: `[slack] client_id`, the `/slack`, `/slack/connect` and
`/slack/callback` pages, and the `/actions/slack/connect` and `/actions/slack/disconnect`
buttons are gone (the pages answer `404`). A config that still sets `client_id` is refused,
saying it's gone: delete the line. To keep looking up reviewers' Slack ids by email, give slack
a token with the `users:read.email` scope, with `mfd token set slack` or `$SLACK_API_TOKEN`.
Why a lookup failed is now said on `/slack/contacts`. If you had connected, the app's tokens
stay in the system's secret store under `move-forward-slack-app` and `move-forward-slack-refresh`
(your `keychain_service`, then `-app` and `-refresh`), for your `email`, and nothing reads them
now. Delete them with `secret-tool clear service move-forward-slack-app account <email>` on
Linux, `security delete-generic-password -s move-forward-slack-app -a <email>` on macOS, or
`cmdkey /delete:move-forward-slack-app` on Windows (and the same for `-refresh`), and remove
the app from the workspace if nobody uses it. The connection's state in the database is left
too, unread. A connection made before the app had entries of its own kept
its token under `keychain_service` itself, where slack now reads it as the token given by hand:
replace it with `mfd token set slack`.

### `mfd notes remember` and `recall` replace the skill's bookkeeping commands

`mfd notes decline`, `checked`, `asked-done`, `offered`, `offers`, `asked` and `last-asked`
are gone. `mfd notes remember <key> [--on YYYY-MM-DD]` records the day something happened
(today by default), and `mfd notes recall <key>` prints that day, or an empty line when
there's none (`--json`: `{"key": …, "on": "YYYY-MM-DD" or null}`). What the old commands
kept is copied over under these keys, an entry named by its title:

| Was | Now |
| --- | --- |
| `mfd notes checked <entry> [--on D]` | `mfd notes remember "checked:<entry title>" [--on D]` |
| `mfd notes asked-done <entry> [--on D]` | `mfd notes remember "asked-done:<entry title>" [--on D]` |
| `mfd notes decline <entry> <text>` | `mfd notes remember "decline:<entry title>:<text>"`; turned down today when `recall` gives today |
| `mfd notes offered <PR URL> <kind>` | `mfd notes remember "offered:<PR URL>:<kind>"` (`nudge`, `fresh-review`) |
| `mfd notes offers --pr <URL>` | `mfd notes recall "offered:<URL>:nudge"`, and `…:fresh-review` |
| `mfd notes asked <question> [--on D]` | `mfd notes remember "asked:<question>" [--on D]` |
| `mfd notes last-asked <question>` | `mfd notes recall "asked:<question>"` (empty, not `never`, when it wasn't) |

An entry was named by its id, title, PR or ticket; a key names it by its title as it is now,
which `mfd notes show <entry> --json` gives, and is matched exactly (but for the space around
it). Every key is forgotten `prune_days` after its day: check-ins and asked-done days too,
which were kept as long as their entry, and once-a-day questions, which were kept forever.
Read an empty `recall` as "not lately", not "never". Once `asked:ice` is forgotten, `mfd notes
export` writes `on-ice.md` without its `Last asked` line. An offer is now kept by its day rather
than its time.
`mfd notes offers` without `--pr` has no replacement: recall the PRs you're about to offer on.
The entries in `mfd notes list --json` and `show --json` no longer have `checked_on`,
`asked_done_on` or `declined`, and `show` no longer prints them: recall their keys instead.
An import keeps a work-notes `- Declined <date>: …` line as a line of its entry. Change the
work-notes skill to use the new commands.

### Each mail account says its own mailbox

A `[mail.<name>]` account no longer takes what it leaves out from `[mail]`: each says its own
`host` and `username`, and its `port`, `mailbox`, `sent_mailbox`, `message_url` and
`auth_servers` where it doesn't want their defaults (993, `INBOX`, the server's sent mailbox,
no link, and the servers known for its host). With accounts, `[mail]` sets none of those, only
what's about all mail (`refresh_seconds`, `known_days`, `since_days`, `max_messages`,
`urgent_words`, `low_words`). A config whose `[mail]` still sets one alongside accounts is
refused, naming the key and the accounts that went without it: copy the key into each of those
accounts it was for, then delete it from `[mail]`. Passwords were already each account's own,
but `[mail]`'s `token_env` and `keychain_service` were ignored alongside accounts; now they're
refused: set them in the account they were for, and remove them from `[mail]`.

### A page load no longer refreshes

`refresh_on_load_seconds` is gone, and with it the refresh a page load asked for of every
plugin whose data was older than that (60 seconds by default). Each plugin still refreshes on
its own `refresh_seconds`, and sooner when its tool has something new; the Refresh buttons
and an item's ↻ refresh now. A config that still sets `refresh_on_load_seconds` is refused,
saying it's gone: delete the line. A script that loaded `/` to make the dashboard refresh
should `POST /refresh` instead, or run `mfd refresh`.

### One key for how far back commits count

`[github] reviewer_history_days` is gone: reviewer suggestions now count commits as far back
as `[git] commit_history_days` (180 by default), which already said how far back my commits
count towards the directories I work in. A config that still sets `reviewer_history_days` is
refused, saying where it went: delete the line, and if you had changed it, set
`commit_history_days` in `[git]` instead.

### Focus mode walks Act now or one panel

Focus mode no longer has All or a walk of a plugin's group: `/focus?cat=all` and
`/focus?cat=<plugin>` (e.g. `cat=github`) now answer `404`. Link to `/focus?cat=act` for Act now,
or to a panel's walk, `/focus?cat=<plugin>.<panel>` (e.g. `cat=notes.todos`), which are
unchanged. The list at `/focus` shows each group's title without a link.

### `mfd snooze` takes any entity, as the Snooze button does

`mfd snooze <entity> [7d|12h] [--kind K] [--task T]` names what to snooze as `mfd priority` and
`mfd unsnooze` do (a PR's URL still works), and snoozes the items about it that have a Snooze
button, in Act now or a panel, the way the button does: each comes back when it changes (a PR's
review, when its head commit moves, as before) or when the time is up. Without a time, the
snooze lasts `[act_now] snooze_days` for the entity's kind, as the button's does. For one of
its tasks only, add `--task`, e.g. `--task review`. It exits 1, saying so, when nothing about
the entity has a Snooze button (already snoozed, or not shown), where it used to need a PR the
dashboard knew. It prints "snoozed N item(s) until …, or until each changes".

### No "Not important" priority

A priority is now important (1) or critical (2). `mfd priority <entity> 0` and
`POST /actions/act_now/priority` with `level` 0 are refused, and the Not important button (❕) is
gone. Priorities already set to 0 are deleted when the dashboard next starts. To set work
aside, snooze, dismiss or put it on ice instead; a script that set 0 should do one of those.

### No more vcard plugin

The vcard plugin, which read an address book's vCard files, is gone. A config with a `[vcard]`
section is refused when it loads: delete the section. Say who matters with contact types
instead of its `group_importance` and `group_types`: Set type on an item about them, or
`mfd contact <address|@domain> <type>` (see the README's People). Its tables (`vcard_*`) stay
in an existing database, read by nothing; they're harmless, and can be dropped by hand.

### Jira no longer tells of comments and mentions

The `jira.comment_added` and `jira.mentioned` notification kinds are gone: Jira already tells
of both itself, through Slack. A config that still subscribes to either is refused when it
loads: take it out of `[notifications] subscribe`.

### `mfd slack contacts` is now `mfd slack destinations`

It clashed with `mfd contacts`, which prints contact types. Its output and `--json` are
unchanged: call `mfd slack destinations` instead.

### `[jira.teams]` is gone

Jira never used it. A config that still has a `[jira.teams]` table is refused when it loads:
delete the table.

### Canceled browser requests no longer print tracebacks

Closing a tab or canceling a page load while the server answers no longer prints a
broken-pipe or connection-reset traceback. Disconnects are logged only with `-v`;
other request failures still surface. Run `mfd restart` to load the fix in a running
server, unless `mfd serve --reload` has already picked it up. No config change is needed.

### No more HTTPS mode

The dashboard serves plain HTTP on localhost only. A config that sets `https` (true or false)
is refused when it loads: delete the line. `mfd cert` is gone, and so is the
`dashboard.certificate_ending` notification kind: take it out of `[notifications] subscribe`.
Reach the dashboard at `http://mfd.localhost:<port>` (`mfd open` does). The `tls/` folder next
to the database is no longer read, and can be deleted, as can the authority `mfd cert --trust`
added to the system's and browsers' stores.

### Mail's server settings are set in the config file only

`POST /settings` no longer changes `[mail]` `host`, `port`, `token_env`, `keychain_service`,
`message_url` or `auth_servers` (nor an account's): it answers "nothing changed" and leaves the
file alone, as for the other plugins' credential settings. Set them in the config file.

### Text in UTF-8 everywhere

`mfd` prints UTF-8 even when its output is redirected (on Windows it used the code page, and
failed on an emoji), and a config file saved with a byte-order mark loads. A script on Windows
reading `mfd`'s plain text output should read it as UTF-8; `--json` is unchanged.

### Several mailboxes

Each sub-table of `[mail]` (`[mail.home]`, `[mail.work]`) is a mailbox of its own, taking from
`[mail]` what it leaves out, with its own password (`mfd token set mail.work`, or
`MAIL_PASSWORD_WORK`). A lone `[mail]` is read as before: nothing to change.

### Tokens for a plugin's accounts

`mfd token set <plugin>.<account>` stores the token of one of a plugin's accounts, for a plugin
reading several, and `mfd token check` lists each account's apart. An unknown name's error now
lists the names there are. Nothing to change for one token per plugin.

### Two plugins can't keep their tokens under one keychain service

A config whose sections name the same `keychain_service` is now refused when it loads, as
storing one token (`mfd token set`, Slack's Connect) would replace the other's. Give each
section its own.

### Merge when ready

`POST /actions/github/auto_merge` with `pr` and `head` turns a PR's auto-merge on, and
`/actions/github/cancel_auto_merge` with `pr` turns it off, as Ready for review's Merge when ready
and Cancel auto-merge buttons do; the first only while its base requires a review it hasn't
had. Nothing to change.

### Merge

`POST /actions/github/merge` with `pr` and `head` merges a PR ready to merge, or adds it to its
merge queue, as Ready to merge's Merge and Add to queue buttons do. A new `[github.merge]`
`method` says how, where a repo allows several. Nothing to change.

### Restack

`POST /actions/git_stack/stack_restack` with `repo` and `branch` runs `git stack restack` on its
stack, locally, as Stacks' Restack button does. Nothing to change.

### Pull

`POST /actions/git/pull_branch` with `repo` and `branch` fast-forwards a branch to its upstream,
as the Pull button on "moved on GitHub; pull it" items does. Nothing to change.

### New todo

`POST /actions/notes/todo_new` with `input` (a leading `[quick]` or `[big]` sizes it; or
`choice`: `quick` or `big`) starts a
todo from nothing, as an entry titled by it, as the Todos panel's New todo field does; it answers
"already a todo" for one already open there. It and `todo_add`'s `create` refuse a title with a
line break, or the work notes' "Reviews requested of me". `todo_add` and `mfd notes todo add`
take a leading `[quick]` or `[big]` in the text as its size, as the notes write it. Nothing to
change.

### Rerun failed checks

`POST /actions/github/rerun_failed` with `pr` (one of my open PRs) runs the failed jobs of its
failing checks' finished Actions runs again, as CI failures' Rerun failed button does. Nothing
to change.

### Tags

`#tags` in titles and descriptions (out of code) relate items, and any item can be tagged by hand: `mfd tag <entity> <tag>
[--kind K] [--remove]`, or `POST /actions/act_now/tag` (`kind`, `id`, `input`) and
`/actions/act_now/untag` (`kind`, `id`, `tag`). Items show their tags as badges. Nothing to
change; scripts reading items' badges or buttons will see `#tag` badges and a Tags group. A
tag from a tool's label is a badge object, `{"text": "#bug", "tone": "label"}`, not a string.

### Mail

A `[mail]` section reads a mailbox's unread and flagged mail over IMAP, headers only and
read-only: `host` and `username` are required; the password comes from `mfd token set mail` or
`$MAIL_PASSWORD`. `/mail/open?thread=` sends the browser to a message in a mail app. See
docs/plugins/mail.md. Nothing to change unless you add the section.

### Size a todo on the page

Add todo has a size drop-down, and open todos have Quick and Big buttons, posting `todo` and `size`
to `/actions/notes/todo_size`. Sized quick, a todo can make its entry a close-out in Act now.
Nothing to change.

### Contact types

`mfd contact <address|@domain> <type|clear>` and `mfd contacts` set and list what people are to me,
and `[people] contact_types` says what each type is worth. Scripts can post `choice` and `input` to
`/actions/act_now/contact`, and `who` to `/actions/act_now/contact_clear`. Nothing to change.

### Update a PR's branch from the page

A PR held as behind its base now has an Update branch button. Scripts can post `pr` to
`/actions/github/update_branch`. Nothing to change.

### Take a draft out of draft from the page

A draft PR housekeeping says may be ready now has a Ready for review button. Scripts can post `pr`
to `/actions/github/mark_ready`. Nothing to change.

### Say why something is on ice

On ice items have a field to say why, in place of "iced from the dashboard". Scripts can post `ice`
and `input` to `/actions/notes/ice_note`. Nothing to change.

### Clear a team's channel on the contacts page

The contacts page has a Clear button for a team with a channel, which asks first. Scripts can post
`team` to `/actions/slack/clear_team_channel`. `set_team_channel` with both fields blank still
fails. Nothing to change.

### Merged stack branches reach Act now

A merged branch git-stack tracks now reaches Act now as "Clean up", and has a Clean button on its
Stacks item, as other merged branches have Delete. Nothing to change.

### Put on ice from Stacks ices the stack

Put on ice on a Stacks item now ices the branch's whole stack, as it does from Local work. It used
to ice only the PR, or offer nothing for a branch without one. Nothing to change.

### Setting an Act now item aside covers all it shows

Snooze, Dismiss and Pin now also act on the item's `also:` tasks, so snoozing a ticket no longer
brings it straight back with its next task. A task that changes still comes back by itself.
Scripts posting these buttons' fields as given by `mfd dump` need no change.

### Act now keeps the buttons of what's also due

An item's `also:` tasks now bring their buttons, so a ticket's Move to In Review stays usable under
its What next item. In `mfd dump`, such an item has more `actions`. Nothing to change.

### Tick off an entry's several todos in Act now

A close-out with several todos now lists them in Act now, each with its Done box. In `mfd dump`,
such an Act now item has them under `parts`, as the todos panel's does. A close-out with several
todos that's snoozed or dismissed comes back once, as what's kept for it changed.

### An entry's several todos are only in its `parts`

In `mfd dump`, the todos panel's item for an entry with several open todos no longer repeats them
in `details`: read its `parts`, which have each todo's id and buttons. An entry with one open todo
still has it in `details`.

### Request a reviewer from the page

A PR missing a reviewer now reaches Act now as "Request a reviewer", below anything else to do on
it. It has a field for one in Needs a reviewer and Fix before review. Scripts can post `pr` and `input` (a login) to
`/actions/github/request_reviewer`. Nothing to change.

### `due_warning_days` also decides which Jira tickets reach Act now

Jira nominated tickets due within a fixed week, whatever `[act_now] due_warning_days` said.
It now follows the setting, so a longer one brings earlier tickets into Act now and a shorter
one leaves them out until nearer their date. Nothing to change unless you set it and relied on
the week.

### Pick a PR's primary reviewer

For a repo whose PRs need a primary reviewer as an assignee, list it in `[github]
primary_reviewer_repos`, e.g. `["acme/widgets"]`. Your open PRs there then show who it is and
have a field to pick them. Scripts can post `pr` and `input` (a login) to
`/actions/github/primary_reviewer`. Other repos are unaffected.

### Set an entity's priority over HTTP

`POST /actions/act_now/priority` with `kind`, `id` and `level` (0, 1 or 2) sets how much an
entity matters, as `mfd priority` does; leave out `level` to clear it. Scripts can use it
instead of running the command; nothing needs changing.

### Snooze and Clear priority wherever an item is

Every item with something to do now has Snooze in its own panel, and Clear priority once one is
set. An item snoozed or dismissed, there or in Act now, stays in its panel, last and dimmed, with
Unsnooze (or Undismiss), until it changes or the snooze ends; it used to leave Ready for me to
review and Replies for me. In `mfd dump`, such an item has `set_aside` (`snooze` or `dismiss`).
`POST /actions/act_now/unsnooze` (the Snooze button's fields) brings it back, and `mfd unsnooze
<entity> [--kind K] [--task T]` now takes any entity, as `mfd priority` does, not only a PR's
URL, and brings back what's dismissed too.

### Refresh progress in the tab icon

The refresh icon now highlights the dots of an arrow in sequence instead of spinning.
If a background tab pauses animation, the arrow still indicates a refresh. The header
logo and normal favicon share the wider arrow. Reload the dashboard to load the new
animation code; no config change is needed.

### Recognize review requests after addressed feedback

After pushing changes and requesting the same reviewer again, a PR whose older review
still says changes requested moves to Ready for review when its failing policy statuses
only say approvals are pending. Fetch the PR again to pick up the status descriptions;
other policy failures still appear in Fix before review.

### Show only direct review requests in Ready for me to review

Ready for me to review now excludes team-only requests. A PR I approved reappears after a
push only when GitHub's review decision still requires review. Find team requests in
Requested from my teams.

### Sort reviews for me by importance

Ready for me to review now shows important and critical PRs before ordinary ones, and
not-important PRs last; recent updates decide the order within each level.

### Finish work-note todos from the Notes panel

Entries with several open todos now show each todo and its Done checkbox on the dashboard.
Use that checkbox to complete a todo without opening Focus mode; the other todo actions
are available there too.

### Restart a supervised server on demand

Run `mfd restart` to ask a running `mfd serve` supervisor to check and replace its server
process without editing the config. On macOS and Linux, sending `SIGUSR1` to the
supervisor process has the same effect. Restart an existing `mfd serve` once to load
this new supervisor behavior.

### Importance choices on entity items

Every item with an entity subject, but what's on ice, now has the same Not important, Important and Critical
buttons as Act now, including GitHub reviews for me and todos in Focus mode. The choices
appear together with ❕ / ❗️ / ‼️ and the selected level highlighted. Act now's Snooze, Dismiss and Pin
buttons, Put on ice, and local branches' Delete and Put on ice buttons are grouped with emoji icons;
their labels appear in the button titles and accessible names.
No config change is needed.

### GitHub review stages and team reviewer rules

GitHub now groups open, non-draft PRs into Fix before review and Ready for review before
Ready for me to review. Mentions without a review request are in Replies for me. Pending
CI does not hold a PR out of Ready for review. To require a
requested member of one team alongside its team request, set
`team_reviewer_assignment = { bacon = "individual" }` in `[github]`; the default
`default_team_reviewer_assignment = "team"` accepts a team request on its own.

### Notifications reach inactive tabs without a browser timer

The dashboard now uses `GET /notifications/stream` to receive browser notifications as
server events; `POST /notifications` still works for older clients. A batch contains up to
20 distinct things, with repeated events about one PR combined. Scripts using
`POST /notifications` should expect fewer duplicate alerts and may receive more than 20
events combined into those notifications.

### Opened items are dimmed, not hidden

Opening an Act now item's link dims it in its place until it changes, instead of hiding it
until its plugin fetches again. `/actions/act_now/pending` is now `/actions/act_now/opened`
(same fields). What was hidden that way is dimmed after the upgrade.

### Fetching one PR again

`mfd refresh --subject pull_request <URL>` fetches just that PR (any number of
`--subject KIND ID`), instead of every search; `POST /refresh/subjects` does the same with
`[{"kind": "pull_request", "id": "<URL>"}]`. Nothing to change; a faster way to see a PR
you just acted on.

### Plugins' pages live under the plugin's name

A plugin's pages are served at `/<plugin>/<page>`: slack's ping page moved from
`/ping?pr=…` to `/slack/ping?pr=…`. Its other pages (`/slack`, `/slack/contacts`,
`/slack/connect`, `/slack/callback`) keep their URLs, so the Slack app's redirect URL is
unchanged. Update a bookmark or link to `/ping`.

### Act now keeps snoozes, dismissals and pins by task, not verb

Each item Act now shows has a `task`, a stable id for what doing it is (`review`, `merge`,
`transition`, `close_out`), beside its `verb`, the words shown ("Move to In Review").

- `mfd snoozes` JSON: `verb` is now `task`, e.g. `"task": "review"`.
- `/actions/act_now/*`: the field `verb` is now `task`, and `also`'s entries are
  `[kind, id, task, fingerprint]`.

What was kept by verb gets its task when the dashboard next opens the database: `Review` is
`review`, `Fix CI` `fix_ci`. Jira's "Move to …" becomes `transition`, slack's pings `ping`, and
notes' "Finish N quick todos" `close_out`, so a status rename or a count changing no longer
drops a snooze. A notes close-out of a single todo, kept by the todo's text, lapses once.

### Tickets are known by their URL, people by `<host>/<login>`

A ticket's id is now its URL (`https://acme.atlassian.net/browse/APP-12`), not its key,
and a person's is `<host>/<login>` (`github.com/alice`), not the login, so two trackers'
`APP-12` or two hosts' `alice` are never taken for one. The key and the login are still
there, as the entity's `handle`.

Where you'll meet them:

- `mfd notes list` and `show` JSON: a ticket link's `ref` is the ticket's URL. `mfd notes
  show`'s text prints it too.
- `mfd notes ices` JSON: an iced ticket's `ref_id` is its URL.
- `mfd snoozes` JSON: an item about a ticket has the ticket's URL as its `id`.
- `/actions/notes/ice_item` and `/actions/act_now/*`: the page sends tickets' URLs.

What still takes a key: `mfd priority APP-12 1`, `mfd notes ice --item ticket APP-12`,
`mfd notes show APP-12`, `mfd notes link <entry> APP-12` and `mfd notes todo link <id>
APP-12` find the ticket by its key, as before.

What the dashboard kept by a ticket's key (priorities, Act now's feedback, the notes'
links and what's on ice) gets the ticket's URL at the next refresh, once jira is set up.

### Entity kinds are named for their plugins; pull requests are `pull_request`

A plugin's own kinds now carry its name, and the PR family is spelled out. A family's kinds
(`pull_request`, `ticket`, `person`) are named for the family, whoever publishes them.

| Before | Now |
| --- | --- |
| `pr` | `pull_request` |
| `branch` | `git.branch` |
| `repo` | `git.repo` |
| `stack_branch` | `git_stack.stack_branch` |
| `entry` | `notes.entry` |
| `todo` | `notes.todo` |
| `iced` | `notes.iced` |
| `ticket`, `person` | unchanged |

Where you'll meet them:

- `mfd priority <entity> <level> --kind K` takes the new names (`--kind notes.todo`,
  `--kind git.branch`), and says "PR" for a pull request in its messages.
- `mfd notes ice --item <kind> <id>` takes `pull_request`, `ticket` or `notes.entry`.
- `mfd notes list`, `show` and `ices` JSON: a link's or iced item's `kind` is the new name.
- `mfd snoozes` JSON: `kind` is the new name.
- `/actions/act_now/*` and `/actions/notes/ice_item` take the new names in `kind`.
- `[act_now] snooze_days` keys: `pr = 7` is now `pull_request = 7`, `entry = 2` is
  `"notes.entry" = 2` (quoted, for the dot). The config refuses an old name, saying the new
  one.
- The page's item anchors are built from the kind: `#item-pr-…` is now
  `#item-pull_request-…`, `#item-entry-…` is `#item-notes-entry-…`. Old bookmarks to an
  item open the top of the page.

What the dashboard kept (priorities, Act now's snoozes, dismissals and pins, the notes'
links and what's on ice) is renamed by migrations when it next opens the database.
