# move-forward-dashboard

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
~/src/move-forward-dashboard/dev-install.sh   # .venv, `mfd` on PATH, git hooks, then `mfd init`
mfd serve --open
```

`dev-install.sh` ends with `mfd init` when there is no config yet: it asks for
each setting on the command line, with a guess from the machine as the default
(the GitHub org of the current repo's remote, the teams `gh` says you're in,
your git email's domain), and writes
`~/.config/move-forward-dashboard/config.toml` with the rest of the settings
as commented defaults. `mfd init --example` writes a commented example
without asking; `--force` replaces an existing file. The required keys are
`org`, `repos` (your main checkouts; other worktrees are found from them),
`notes_dir` (where `work-notes.md`, `on-ice.md` and `reviewer-map.md` live:
the move-forward skill's notes, which the dashboard reads on every refresh
and writes todos and icing to; any directory works without the skill) and
one `[[teams]]` table per GitHub team or review group you review for, with
its `github_slug` and optionally the `slack_channel` review pings go to.
`slack_workspace_url` makes the ping page link to channels and people;
`email_domain` lets it look up reviewers' emails from their commits.

Conventions that differ between organisations are settings, off by default:
`review_checks` (CI checks that really report review status),
`required_owner_marker` (a CODEOWNERS comment such as `#required`),
`ticket_link_prefix` (the PR body line that links the ticket, e.g. `Link`),
`placeholder_labels` (template labels to replace), the ping message
templates, and under `[jira]` the workflow status names, the escalation
label prefix and levels, and the issue types that count as incidents or bugs.

### Jira

Add a `[jira]` table with `site` and `email`, then either export
`JIRA_API_TOKEN` or store a token in the macOS keychain:

```sh
security add-generic-password -s move-forward-dashboard-jira -a me@example.com -w <token>
```

Create the token at https://id.atlassian.com/manage-profile/security/api-tokens.

## Commands

| Command | Does |
| --- | --- |
| `mfd serve [--open] [--port N]` | Serve the page, refreshing every `refresh_seconds`. |
| `mfd serve --reload` | Development: also restart when a source file of the dashboard changes (`--open` isn't repeated on restart). |
| `mfd refresh` | Refresh every source once, print each source's status, exit. |
| `mfd dump` | Print the dashboard as JSON from what's stored. |
| `mfd init` | Write the example config. |

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

Forms (POST, `application/x-www-form-urlencoded`; each answers `303 See Other`
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
| `/actions/push_branch` | `repo`, `branch` | `git push` (sets the upstream if missing; never forces). |
| `/actions/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`. |
| `/actions/ice` | `repo`, `branch` | Puts the branch's stack on ice. |
| `/actions/thaw` | `branch` | Takes a stack off ice. |
| `/actions/delete_iced` | `branch` | Deletes an iced stack's local branches and thaws it. |
| `/actions/delete_branch` | `repo`, `branch` | `git branch -D`. |
| `/actions/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`) | Deletes each; one failure doesn't stop the rest. |
| `/actions/stack_clean` | `repo` | `git stack clean -d local`; refused while the repo has iced stacks. |

`entry` is a work-notes entry's title exactly as its `##` line has it; `text`
is the todo's text without its `[quick]`/`[big]` marker, exactly as the
focus page shows it (and as `mfd dump` lists it under the todos panel's
items' `parts`). `repo` is one of the configured repo directories. For
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

`dashboard.db` holds each collector's latest snapshot as JSON (kept when a
refresh fails, with the error alongside), the work notes, iced stacks and
reviewer map imported from the skill's `.md` files on every refresh, emails
looked up from commits, and pings marked as sent. The skill's files stay the
source of truth for the notes. The dashboard writes only `on-ice.md`, when a
button puts a stack on ice or thaws one, in the skill's own format.

## Development

```sh
./check.sh            # ruff format --check, ruff check, basedpyright (strict), pytest
.venv/bin/ruff format # fix formatting
```

`dev-install.sh` points the repo at `git_hooks/`: `pre-commit` formats and
lint-fixes staged Python, `pre-push` runs `check.sh`.
