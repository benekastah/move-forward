# Roadmap

What's left to build in move-forward. Only what's still to do lives here: when something
is done, remove it in the same change, and delete this file once it's empty. The code,
`AGENTS.md` and the rest of `docs/` are the reference for what's built.

## Goal

Every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph. The move-forward skill's notes move from markdown files into
SQLite, behind a CLI and the HTTP API.

## Slack as a source of work

Slack becomes a source of work, not just a place pings go. The workspace's Slack app and
signing in to it are built (`/slack`; see `docs/plugins/slack.md`); the features that use
it aren't.

### First, try it with a real app

Nothing so far has met a real Slack app. Before building features on it, someone makes
the app from `manifest.json`, connects, and checks:

- Connect completes, with the `http://localhost:<port>/slack/callback` redirect.
- A refresh works without the client secret (Slack's PKCE guide says so; its token
  rotation guide says the secret is still needed), and whether the refreshed user token
  comes back at the top level or in `authed_user` (slack takes either). Leave it connected
  past 12 hours to see one.
- Disconnect revokes the token.
- For the first features: how `search.messages` finds messages that @-mention me, and the
  rate limits an internal app gets on `conversations.history` and `conversations.replies`.

### How every feature works

- Each feature adds its user scopes to `scopes.py` and `manifest.json` (a test keeps them
  the same) and runs only when the connection was granted them; `/slack` (and `mfd check`)
  names the scope a feature lacks instead of failing in the log.
- Each is a setting in `[slack]`: those that read are on by default once connected
  (`mentions`, `threads`, `saved`, `discussions`, `recognize_pings`, `availability`,
  `reactions`, `rebump_after_changes`); those that send or change something are off by
  default (`summary`, with where it goes and an optional schedule, and `focus_status`).
  Like every config key, each goes in the model, slack's page, `mfd init`'s commented
  defaults and `mfd init --example`.
- Messages are kept as ids, who, when, the channel and a short snippet only, in kept
  tables pruned after `prune_days`. Nothing is ever sent without a click.
- Slack is polled, on slack's own `refresh_seconds`, with `changed` checking cheaply in
  between. Socket Mode can't serve one app shared by many people's dashboards: an app has
  at most 10 connections, each event goes to any one of them, and connecting takes the
  app-level token, a secret everyone would share.

### Features

Reading, roughly in the order to build them:

1. **Waiting on me** (Act now): messages that @-mention me and DMs whose last word isn't
   mine, as "Reply to alice" (quick, alice waiting since the message); threads I'm in or
   started with replies since I last read them; Slack's saved-for-later items and
   reminders, with their due dates.
2. **Pings recognized**: a message *I* sent (DM, thread or team channel) linking one of my
   PRs is recorded as a ping to whoever it reached (first or follow-up, with when), so a
   PR nudged by hand counts as pinged and the next suggestion is a bump, and "mark as sent"
   goes: the ping page copies the message and opens the DM or channel, and the ping is
   recognized once sent. A request for review posted by someone else in a channel shows on
   the PR.
3. **Reactions to pings**: any reaction to my ping but 👎 acknowledges it (no bump); ✅
   shows on the PR as "alice ✅ in Slack" and ❌ as "alice ❌ in Slack", and both stop the
   nudges. Slack's reactions never stand for GitHub's review state: Ready to merge still
   goes by GitHub's approval. A reaction holds nudges off only until the PR changes: a
   push after it makes the PR ready to bump again, since it likely needs a fresh look
   (`rebump_after_changes`, on by default).
4. **Following github**: slack `watches` github, so right after each github refresh its
   `changed(ctx, graph)` compares my open PRs and their requested reviewers with what it
   saw last, and a new request or reviewer has slack look at once for that PR's pings and
   reactions (only the PRs that moved).
5. **Messages in the graph**: slack publishes a `message` kind mentioning its text, so a
   message pasting a PR URL or naming a ticket key relates to it: PR items show "discussed
   in #team-web", linking there.
6. **Reviewer availability**: before suggesting a ping, the reviewer's status (out of
   office, vacation), Do Not Disturb and time zone: "alice is out until Monday; consider
   bob", and nudges held until their working hours.

Sending, off by default:

7. **Daily summary**: what's in Act now and what got done, sent on a click, or on a
   schedule, to me or a standup channel.
8. **Focus sets my status**: entering focus mode sets "Focusing" and Do Not Disturb for a
   while, and leaving it clears them.

## Buttons and consistency

An item should offer the same wherever it's shown, and what the page tells me to do should
come with a button where the dashboard can do it. Each step here stands alone; a plugin step
that needs a core one says so, and lands on top of it.

### Core

1. **Snooze and Clear priority everywhere.** Every item with a task gets Snooze in its own
   panel, drawn as in Act now (💤, grouped), which replaces `Panel.snoozable`; and every panel
   gets Clear priority once one is set. Pin stays in Act now: it only concerns that ranking.
2. **Undo what's set aside.** A place on the page listing what's snoozed and dismissed, each
   with a button to bring it back. Then Dismiss joins Snooze in the panels, and not before:
   it can't be undone until this exists.
6. **Importance where it means something.** Leave it off items about what hides (On ice). Say
   whether every panel sorts by it, since only two do now, not What next or Feedback.
7. **Focus mode's missing buttons.** Panel-wide buttons (Delete all, Clean) don't show there,
   nor do those of an item that focus mode splits into parts (an entry's Put on ice).
10. **Account on the settings page.** A Sign out everywhere button (`mfd logout`), and where
    each plugin's token is found (`mfd token check`). Setting or rotating a token stays in
    the terminal.

### github

Each button runs `gh`, checks its fields (a PR on `host`, a login's form), and returns the PR
as `touched`, so it's fetched again at once.

12. **Merge.** On Ready to merge, with a confirm: `gh pr merge --match-head-commit <the head
    shown>`, so a push since then refuses it. A merge queue takes no method, and the button says
    Add to queue. Otherwise the method comes from a setting or the repo's default. Never
    `--admin`, and the branch stays for Cleanup.
14. **Ready for review.** `gh pr ready`, with a confirm, on a draft housekeeping says may be
    ready.
15. **Update branch.** `gh pr update-branch` (merging, not rebasing, which would rewrite what's
    stacked on it) on a PR held as behind its base.
16. **Run failed checks again.** `gh run rerun --failed` on CI failures, for checks whose URL
    names a run.

Approving stays on GitHub: a review should come from having read the PR there.

### git and git_stack

18. **Pull.** On a branch whose PR moved on GitHub ("pull it"), fast-forward only, and refused
    with local changes in the way.
20. **Restack.** `git stack restack`, with a confirm, on a branch that needs it and on a conflict
    that says to. It stays local: `sync` and `push` force-push (with lease), which the rule
    against force-pushing from a button forbids unless that's changed.

### jira


### notes

24. **Size a todo.** Dashboard todos are all unsized, so never close-outs. `todo_add` already takes
    `size`; give it a control, and a way to size an existing todo (there's none, in the CLI
    either).
25. **Edit an entry.** What the CLI can do to one: `blocked-on` (its badge can't be cleared from
    the page), status and where (not shown at all), `link`, and `remove`.
26. **Merge conditions.** Held's "held: …" has no way out but `mfd notes condition remove`. Add
    and remove them on the PR's items.
27. **Start a todo from nothing.** The docs call notes alone a todo list, but a todo needs a PR
    or ticket to start from on the page. A New entry button on the panel (`mfd notes add-entry`).
28. **Undo a tick on the dashboard.** Focus mode keeps a ticked todo to untick; the dashboard loses
    it. Keep recently done todos, ticked, for a while.
29. **The same todo buttons.** Each of an entry's several todos repeats its entry's Add todo.
30. **Why it's on ice.** The button always records "iced from the dashboard"; `ice` already takes a
    `note`, so ask for one.

### slack

32. **Clear a team's channel.** `/slack/contacts` can't (`mfd slack team unset`).

## Follow-ups

### Refreshing

- **An opt-in fetch in git.** git never fetches, so remote branches move only when I fetch
  (by hand, `git pull`, an editor's auto-fetch). PR checkouts that fell behind already show
  without it (from github's PR heads), but branches without a PR don't, and remote
  branches deleted after a merge linger until a `fetch --prune`, so Cleanup's "remote
  gone" lags. A `fetch_seconds` setting (off by default) would run `git fetch --prune
  --quiet` per repo on its own schedule: without credential prompts
  (`GIT_TERMINAL_PROMPT=0`), with a timeout, failing per repo without failing the refresh.
  git's change check sees what it brings, and github follows. It's the first time the
  dashboard would write to the repos without a click (only remote-tracking refs), hence
  opt-in.

- **Faster fallback polling with git's fsmonitor.** A worktree git can't watch (over
  Linux's inotify watch limit, say) has its `git status` polled every `status_seconds`. In
  a repo with `core.fsmonitor` on, that status is cheap, so it could be polled more often
  there. Only worth it once someone actually lands in the fallback.

### The page

- **First-run setup in the browser.** `/settings` edits a config that's there; `mfd serve`
  without one could open a form to make it (still behind the token and same-site checks,
  writing only the config file), filling in what can be fetched as it goes (the org's
  teams once the org is given, like `mfd init`'s tab completion). Tables (`[github.teams]`,
  `[slack.teams]`) could join the settings page then too.
- **Act now, after living with it.** Questions to answer from use: whether quick wins
  should lead at the end of the day and important work in the morning (a weight profile
  per part of the day); whether someone waiting on my team should weigh more (the `person`
  entities would need team membership, which github already fetches); whether unknown
  effort should count as "hour"; and whether notes' todos should carry `due` and
  `waiting_since` (the notes don't record due dates yet).

### Code hosts

- **A second code host.** `repo` ids are GitHub `owner/name`, from a GitHub-only remote
  regex. A second host (Bitbucket, GitLab) needs ids like `github.com/acme/widgets` so
  they can't collide, and git parsing any host. Its plugin would publish its own kind of
  PR (one plugin per kind), and every consumer would read both; if that gets repetitive, a
  PR could become a contract several plugins publish to, with a shared base for code-host
  plugins. All of it waits for that second host.

### Notes and the skill

- **Move the skill onto the notes' fields.** Check-ins, offers, once-a-day questions and
  per-PR merge conditions are in the notes now (`mfd notes checked`, `asked-done`,
  `offered`, `asked`, `condition add --pr`); the skill still keeps them its old way until
  it's changed to use them. Its `reviewer-map.md` is read only through `mfd slack import`
  and `mfd github import`, so it should set Slack ids with `mfd slack contact set` rather
  than editing the map.
- **Keep the skill and the dashboard in step.** The CLI's JSON output is the contract the
  skill is written against: change it deliberately, with the skill. The skill's own rule
  against cleaning while something's on ice no longer matches the dashboard, which cleans
  regardless (`git stack clean` never restacks or pushes).

### Windows, on a real machine

CI passes on Windows, but these need trying by hand: `py dev.py install` (the PATH
change, the `mfd.cmd` shim, the hooks through `py -3`); `mfd cert --trust` with
`https = true`, then Edge, Chrome and Firefox (which should read the Windows store);
`mfd serve` replacing its server on a config change, and ctrl-c stopping it;
`mfd token set` with Credential Manager; Slack's Connect; git-stack itself; git watching its
repos while you work (the watcher holds handles on the watched directories, and Windows can
refuse to delete or rename a directory with handles open in it: `git worktree remove`, a
checkout that deletes a top-level directory, renaming a repo's folder); and repo paths
compared as `Path`s (case, `~`, separators), including those stored in `notes_ice`. On a
Windows that isn't in English, `icacls` names SYSTEM and Administrators in its language,
so `private.is_private` wouldn't recognize them and the token file would be warned about
on every start: look their names up from their well-known SIDs (`LookupAccountSidW`)
rather than matching English ones.
