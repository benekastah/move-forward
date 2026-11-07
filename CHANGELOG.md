# Changelog

What changed for anyone driving `mfd` from outside: its commands, their JSON, the config,
the HTTP endpoints and the page's links. Newest first. Each entry says what to do about it.

## Unreleased

### Finish work-note todos from the Notes panel

Entries with several open todos now show each todo and its Done checkbox on the dashboard.
Use that checkbox to complete a todo without opening Focus mode; the other todo actions
are available there too.

### Importance choices on entity items

Every item with an entity subject now has the same Not important, Important and Critical
buttons as Act now, including GitHub reviews for me and todos in Focus mode. The choices
appear together with ❕ / ❗️ / ‼️ and the selected level highlighted. Act now's Snooze, Dismiss and Pin
buttons, Put on ice, and local branches' Delete and Put on ice buttons are grouped with emoji icons;
their labels appear in the button titles and accessible names.
No config change is needed.

### Restart a supervised server on demand

Run `mfd restart` to ask a running `mfd serve` supervisor to check and replace its server
process without editing the config. On macOS and Linux, sending `SIGUSR1` to the
supervisor process has the same effect. Restart an existing `mfd serve` once to load
this new supervisor behavior.

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
