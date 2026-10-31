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

Requires Python 3.12+, `gh` logged in, and git. `git stack` is optional (the
Stacks panel is empty without it).

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
`email`, `teams` (the names of the teams you're on or review for), `notes_dir`
(where `work-notes.md`, `on-ice.md` and `reviewer-map.md` live: the
move-forward skill's notes, which the dashboard reads on every refresh and
writes todos and icing to; any directory works without the skill), `port`,
`db_path`, the refresh intervals and `prune_days`. Everything about a tool is
in that tool's plugin section; see [Plugins](#plugins).

Teams are named once, at the top level. A plugin that has its own idea of a
team maps the names in a `teams` table of its section: `[github.teams]` gives
a team's GitHub slug where it isn't the name, `[slack.teams]` the channel its
review pings go to, `[jira.teams]` its id in Jira's team field. A name there
that the top-level `teams` doesn't have is an error.

### Jira

Add a `[jira]` section with `site`; requests log in as the top-level `email`
with an API token. Either export
`JIRA_API_TOKEN` or store a token in the macOS keychain:

```sh
security add-generic-password -s move-forward-jira -a me@example.com -w <token>
```

Create the token at https://id.atlassian.com/manage-profile/security/api-tokens.

## Plugins

Each external tool is a plugin, enabled by its section in the config: leave a
section out and the plugin, its panels and its fetching are off. Plugins learn
about each other's data only through a shared graph of entities (PRs,
tickets, branches, ...), listed in [docs/entities.md](docs/entities.md).

| Plugin | Section | Fetches | Panels |
| --- | --- | --- | --- |
| `github` | `[github]` | my open, merged and closed PRs, review requests, PRs I reviewed, my teams' PRs (with `gh`) | Reviews for me, Feedback, CI failures, Merge conflicts, Needs a reviewer, Ready to merge, Held, PR housekeeping, Requested from my teams |
| `git` | `[git]` | each repo's worktrees, branches, `git stack info` | Local work, Stacks, Cleanup |
| `jira` | `[jira]` | my open tickets | Jira transitions, What next |

The notes (todos, on ice) and review nudges with their Slack links aren't
plugins yet; they're always on.

`[github]` needs `org`. Its conventions that differ between organisations are
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

`[slack]` (`workspace_url`, the ping message templates, `[slack.teams]`) makes
the ping page link to channels and people.

`[jira]` needs `site`; the workflow status names, the escalation label prefix
and levels, and the issue types that count as incidents or bugs are settings.

## Commands

| Command | Does |
| --- | --- |
| `mfd serve [--open] [--port N]` | Serve the page, refreshing every `refresh_seconds`. |
| `mfd serve --reload` | Development: also restart when a source file of the dashboard changes (`--open` isn't repeated on restart). |
| `mfd refresh` | Refresh every source once, print each source's status, exit. |
| `mfd dump` | Print the dashboard as JSON from what's stored. |
| `mfd init [--example] [--force]` | Ask for each setting and write the config (`--example`: write a commented example instead). |

## Panels

The page has six collapsible groups: **Act now** (reviews for me, feedback, CI,
conflicts), **My PRs** (needs a reviewer, nudges, ready to merge, held,
housekeeping), **Local** (local work, stacks, cleanup), **Team**, **Planning**
(todos, Jira) and **Parked** (on ice). Only Act now starts expanded; each
panel inside a group collapses too.

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
- **Nudge reviewers**: PRs in order (green, no conflicts, no open threads) with
  no review activity for `stale_review_hours`. The ping button on any of my
  PRs opens a page with one message per requested reviewer or team, a link to
  their Slack DM or channel, and "mark as sent", which makes the next ping a
  follow-up. Nudges the skill logged in `work-notes.md` count too.
- **Ready to merge** and **Held by merge conditions** (from the work notes).
- **Local work**: dirty worktrees, unpushed and never-pushed branches, with
  buttons: WIP commit (tracked files only), Push (never forced; sets the
  upstream for a new branch), Put on ice.
- **Stacks**: `git stack info` statuses other than up-to-date, and tracked
  branches with no PR, with a button per repo that runs `git stack clean -d
  local` for its merged branches (not offered while the repo has iced stacks,
  since clean touches every stack). The panel only appears when a repo has
  tracked stacks, so without git-stack it's absent.
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

Pages (GET, HTML):

| Path | Shows |
| --- | --- |
| `/` | The dashboard. `?open=act,feedback` sets the open sections; `?notice=…` shows a message. |
| `/focus` | Focus mode's category list. |
| `/focus?cat=<key>&i=<n>` | Item `n` of a category: `all`, a group key (`act`, `my_prs`, `local`, `team`, `planning`, `parked`) or a panel key. `&done_entry=…&done_text=…` keeps a just-ticked todo on the page. |
| `/ping?pr=<PR URL>` | The ping page for one of my PRs. |

Forms (POST, `application/x-www-form-urlencoded`). A plugin's buttons post to
`/actions/<plugin>/<action>`; the rest are core's. Each answers `303 See Other`
to `/`, or to the `back` query parameter when it's a local path, with
`notice=<outcome>` added; a failure is `notice=failed: <reason>`, never an
error status):

| Path | Fields | Does |
| --- | --- | --- |
| `/refresh` | | Starts a background refresh. |
| `/pings` | `pr_url`, `target` (`user:<login>` or `team:<slug>`), `kind` (`first` or `follow-up`), `message` | Records a ping as sent. |
| `/actions/todo_add` | `entry`, `text` (or `input`), optional `size` (`quick` or `big`), optional `create=1` and `where` (PR or ticket URL) | Adds a todo under the entry's `Left to do`; with `create`, a missing entry is created first, with `where` as its `Where`. |
| `/actions/todo_done` | `entry`, `text` | Moves the todo to a `Done <date>:` line; redirects with `done_entry`/`done_text`. |
| `/actions/todo_undone` | `entry`, `text` | Moves a done todo back under `Left to do`. |
| `/actions/todo_delete` | `entry`, `text` | Drops the todo without recording it. |
| `/actions/todo_link` | `entry`, `text`, `input` (Jira key or URL) | Appends the ticket's or PR's URL to the todo. |
| `/actions/git/push_branch` | `repo`, `branch` | `git push` (sets the upstream if missing; never forces). |
| `/actions/git/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`. |
| `/actions/ice` | `repo`, `branch` | Puts the branch's stack on ice. |
| `/actions/thaw` | `branch` | Takes a stack off ice. |
| `/actions/delete_iced` | `branch` | Deletes an iced stack's local branches and thaws it. |
| `/actions/git/delete_branch` | `repo`, `branch` | `git branch -D`. |
| `/actions/git/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`) | Deletes each; one failure doesn't stop the rest. |
| `/actions/git/stack_clean` | `repo` | `git stack clean -d local`; refused while the repo has iced stacks. |

`entry` is a work-notes entry's title exactly as its `##` line has it; `text`
is the todo's text without its `[quick]`/`[big]` marker, exactly as the
focus page shows it (and as `mfd dump` lists it under the todos panel's
items' `parts`). `repo` is one of `[git] repos`. For
example:

```sh
curl -s -o /dev/null -w '%{redirect_url}\n' -X POST http://127.0.0.1:8765/actions/todo_done \
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
source of truth for the notes. The dashboard writes only `on-ice.md`, when a
button puts a stack on ice or thaws one, in the skill's own format.

The schema is built by migrations: core and each plugin own a list, applied in
order at start-up and counted in `schema_versions`, so an older database
upgrades in place.

## Development

```sh
./check.sh            # ruff format --check, ruff check, basedpyright (strict), pytest
.venv/bin/ruff format # fix formatting
```

`dev-install.sh` points the repo at `git_hooks/`: `pre-commit` formats and
lint-fixes staged Python, `pre-push` runs `check.sh`.
