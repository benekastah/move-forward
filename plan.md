# Plan

What's left to build in move-forward. Only what's still to do lives here: when something
is done, remove it in the same change, and delete this file once it's empty. The code,
`AGENTS.md` and `docs/` are the reference for what's built.

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
- **github: update the PRs that changed.** A change check refreshes github whole. Its
  notifications name their PRs, but its panels come from searches, so storing one PR means
  placing it in those lists by hand; worth it only if github's refresh gets slow. (git
  already reads only the repos that moved; jira's full search is one request for most
  people, so a targeted one would save nothing.)

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

- **More signals for which branches are mine.** A PR of mine, my commits off the default
  branch and `my_branch_prefixes` decide it now; git-stack tracking it and being checked
  out in one of my worktrees could count too, if those miss some.
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
- **Clean-ups the daily prune lacks**: notes dropping old declines, and github dropping
  looked-up emails no PR has asked about in `prune_days`. (Finished entries go after 90
  days; the skill removes them sooner with `mfd notes remove`.)
- **Keep the skill and the dashboard in step.** The CLI's JSON output is the contract the
  skill is written against: change it deliberately, with the skill. The skill's own rule
  against cleaning while something's on ice no longer matches the dashboard, which cleans
  regardless (`git stack clean` never restacks or pushes).

### Windows, on a real machine

CI passes on Windows, but these need trying by hand: `py dev.py install` (the PATH
change, the `mfd.cmd` shim, the hooks through `py -3`); `mfd cert --trust` with
`https = true`, then Edge, Chrome and Firefox (which should read the Windows store);
`mfd serve` replacing its server on a config change, and ctrl-c stopping it;
`mfd token set` with Credential Manager; Slack's Connect; git-stack itself; and repo paths
compared as `Path`s (case, `~`, separators), including those stored in `notes_ice`. On a
Windows that isn't in English, `icacls` names SYSTEM and Administrators in its language,
so `private.is_private` wouldn't recognize them and the token file would be warned about
on every start: look their names up from their well-known SIDs (`LookupAccountSidW`)
rather than matching English ones.
