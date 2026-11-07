# Changelog

What changed for anyone driving `mfd` from outside: its commands, their JSON, the config,
the HTTP endpoints and the page's links. Newest first. Each entry says what to do about it.

## Unreleased

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

A PR missing a reviewer, with nothing else to do on it, now reaches Act now as "Request a
reviewer". It has a field for one in Needs a reviewer and Fix before review. Scripts can post `pr` and `input` (a login) to
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

### Snoozed reviews leave their panels

An item snoozed or dismissed in Act now, or snoozed from the panel, also leaves Ready for me
to review and Replies for me until it changes or the snooze ends. A script reading those
panels from `mfd dump` no longer sees it there: use `mfd snoozes` to list what's snoozed.

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

Every item with an entity subject now has the same Not important, Important and Critical
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
