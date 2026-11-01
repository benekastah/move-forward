# Plan

What's left to build in move-forward. Only what's still to do lives here: when a step or
section is done, remove it in the same PR, and delete this file once it's empty. The code,
`AGENTS.md` and `docs/` are the reference for what's built: core and its plugins (github,
git, git_stack, jira, slack, notes), Act now's first version, `mfd init`, HTTPS, per-plugin
refresh intervals, and Windows support with CI on Linux, macOS and Windows.

## Goal

Every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph. The move-forward skill's notes move from markdown files into
SQLite, behind a CLI and the HTTP API.

## Remaining steps

Each step is its own stacked branch, with core changes in a branch of their own ahead of
the plugin change that needs them (see `AGENTS.md`), `dev.py check` green and the page working.

### git-stack: agreeing with what git-stack does

Left to do, in git-stack itself (`../git-stack`; see `git-stack-issues.md`):

- **Narrow clean's clean-worktree check.** `clean` calls `ensure_clean_worktree()` up front,
  but only touches the working tree to fast-forward a checked-out root, or (with `-d local`
  or `both`) to switch off a checked-out branch it deletes. Checking only those two cases,
  still before anything changes, keeps the protection and lets clean run from a feature
  branch with work in progress, the dashboard's usual case.
- **`git stack clean <branch>...` / `-g <group>`**: clean only the named stacks, as `sync`
  takes them. The dashboard could then offer Clean in a repo with iced stacks (cleaning the
  others), and "clean per stack" becomes true.

### 9. Slack, reading and pinging

Slack becomes a source of work, not just a place pings go. It needs a Slack app with a
*user* token (my DMs and mentions, my messages).

**The app is optional.** Without it (not set up, not connected, or the dashboard on plain
HTTP, where Slack's sign-in can't return), slack does all it does today: the reviewer map
and Slack ids (imported, set by hand, or looked up by email with today's token), team
channels, the ping page and recorded pings. None of the features below show, and nothing
breaks. With the app connected, they turn on, each behind its own setting. "Connect
Slack" is offered only when the page is served over HTTPS and the app's client id is
set; on plain HTTP, slack's page says what's missing (`mfd cert`, then connect) instead of
a button that can't work. If the app's token stops working (expired, revoked), slack falls
back to today's behaviour and the page asks to connect again.

**One internal app per workspace.** Workspaces that approve apps would otherwise approve
one app per person. A Marketplace app needs hosting and Slack's review, and Slack
rate-limits the history and replies methods for apps distributed outside the
Marketplace, so the app stays internal. Hosting can come later if it's needed.

- One person creates the app from a manifest checked into the slack plugin (JSON, so
  reading it needs no new dependency) and gets it approved once. The manifest asks for
  every feature's user scopes and no bot scopes. A test checks it against the scopes the
  features below need.
- Everyone signs in to that one app with Slack's OAuth flow, from a "Connect Slack"
  button `mfd serve` shows, and gets their own user token. It uses PKCE, so no client
  secret is shared; the app's client id becomes a new `[slack]` key. Slack only takes
  `https://` redirect URLs, even for localhost, so the redirect is
  `https://127.0.0.1:<port>/slack/callback`, on the dashboard served over HTTPS (`mfd cert
  --trust`). The token goes in the system's secret store, as `mfd token set` stores
  tokens today.
- Not collaborators: a collaborator can manage the app (its scopes, its redirect URLs, its
  credentials), so making a whole company collaborators isn't something to ask an admin
  for, and Slack documents no cap on them either way.
- Slack's page walks through creating and approving the app, connecting, and the scopes
  per feature. On Enterprise Grid, an org-ready app installs across the organization.

Turning PKCE on can't be undone, and Slack expires a PKCE app's refresh tokens after 30
days, so someone who stays away that long connects again. If none of this works, the app
moves to a hosted service.

Messages are kept as ids, who, when, the channel and a short snippet only, in kept tables
pruned after `prune_days`. Nothing is ever sent without a click.

Each feature below is a setting in `[slack]`, so it can be turned off, or on only once the
app has its scopes: `mentions`, `threads`, `saved`, `discussions`, `recognize_pings`,
`availability`, `reactions`, `rebump_after_changes` (reading, on by default once there's
a token), and
`summary` (with where it goes and an optional schedule) and `focus_status` (sending or
changing something: off by default). A feature whose scopes the token lacks says so on the
page (and in `mfd check`), naming the scope, instead of failing in the log. Like every
config key, each goes in the model, slack's page, `mfd init`'s commented defaults and
`mfd init --example`.

- **Waiting on me** (Act now): messages that @-mention me and DMs whose last word isn't
  mine, as "Reply to alice" (quick, alice waiting); threads I'm in or started with replies
  since I last read them; Slack's saved-for-later items and reminders (with their due
  dates once Act now has `due`).
- **Messages in the graph**: slack publishes a `message` kind mentioning its text, so a
  message pasting a PR URL or naming a ticket key relates to it: PR items show "discussed
  in #team-web", linking there.
- **Pings recognized**: a message *I* sent (DM, thread or team channel) linking one of my
  PRs is recorded as a ping to whoever it reached (first or follow-up, with when), so a
  PR nudged by hand counts as pinged and the next suggestion is a bump; "mark as sent"
  goes. A request for review posted by someone else in a channel shows on the PR.
- **Reviewer availability**: before suggesting a ping, the reviewer's status (out of
  office, vacation), Do Not Disturb and time zone: "alice is out until Monday; consider
  bob", and nudges held until their working hours.
- **Pinging from the page**: Slack links can open a DM or channel but not pre-fill a
  message, so the ping page copies the message and opens the DM or channel in Slack; the
  ping is recognized once sent (above).
- **Reactions to pings**: any reaction to my ping but 👎 acknowledges it (no bump); ✅
  shows on the PR as "alice ✅ in Slack" and ❌ as "alice ❌ in Slack", and both stop the
  nudges. Slack's reactions never stand for GitHub's review state: Ready to merge still
  goes by GitHub's approval. A reaction holds nudges off only until the PR changes: a push
  after it (after ✅, ❌ or an acknowledgement alike) makes the PR ready to bump again,
  since it likely needs a fresh look or approval (`rebump_after_changes`, on by default).
- **Daily summary**: what's in Act now and what got done, sent (on a click, or on a
  schedule I set) to me or a standup channel.
- **Focus sets my status**: entering focus mode sets "Focusing" and Do Not Disturb for a
  while, and leaving it clears them.
- **Changes as they happen**: Socket Mode, a connection the dashboard opens itself, brings
  Slack's events as they happen, so slack implements `changed` (see the other plugins)
  without polling.
- **Following github**: slack `watches` github, so right after each github refresh its
  `changed(ctx, graph)` compares my open PRs and their requested reviewers with what it
  saw last. A new review request or a new reviewer has slack look at once for the pings
  and reactions about that PR (pings recognized, reactions to pings), rather than waiting
  for its own schedule. The PRs that moved are the ones to look up, not all of them.

## Follow-ups

- **An opt-in fetch in git.** git never fetches, so remote branches only move when I fetch
  (by hand, `git pull`, an editor's auto-fetch). PR checkouts that fell behind already show
  without fetching (from github's PR heads), but branches without a PR don't, and remote
  branches deleted after a merge linger until a `fetch --prune`, so Cleanup's "remote
  gone" lags. A `fetch_seconds` setting (off by default) would run `git fetch --prune
  --quiet` per repo on its own schedule: without credential prompts (`GIT_TERMINAL_PROMPT=0`),
  with a timeout, failing per repo without failing the refresh. git's change check sees
  what it brings, and github follows. It's the first time the dashboard would write to the
  repos without a click (only remote-tracking refs), hence opt-in.
- **git_stack watches git instead of riding along with it.** git_stack collects after git
  and `requires` it, so every git refresh (once a minute for uncommitted edits, and after
  every local change) runs `git stack info` in every repo, even when no branch moved. It
  could declare `watches = ["git"]` and a `changed(ctx, graph)` comparing git's `branch`
  entities (tips, upstreams) with what it saw last, and collect only when they moved,
  with its own `refresh_seconds` as the backstop. First check where git-stack keeps its
  stack metadata: what it keeps outside git's refs wouldn't show in git's branches, and
  would need a check of its own (that file's time).
- **Act now, after living with it.** Questions to answer from use: whether quick wins
  should lead at the end of the day and important work in the morning (a weight profile
  per part of the day); whether someone waiting on my team should weigh more (the `person`
  entities would need team membership, which github already fetches); whether unknown
  effort should count as "hour"; and whether notes' todos and slack's pings should carry
  `due` and `waiting_since` (the notes don't record due dates yet).
- **Update what changed, not the whole plugin.** A change check now refreshes the whole
  plugin. jira's check could ask for the changed tickets' fields and store just those,
  and git's fingerprint could be kept per repo, so only the repo that moved is read
  again. github's notifications name their PRs, but its panels come from searches, so
  storing one PR means placing it in those lists by hand; that one can wait. The
  periodic full refresh keeps any of these honest.
- **GitHub Enterprise.** github assumes github.com: a `host` key, passed to every `gh`
  call (`--hostname`) and used for PR URLs, would let it work with an Enterprise server.
  `mfd init`'s org question already takes a pasted org URL; it would fill in both the host
  and the org.
- **Settings from the page.** `mfd serve` restarts when the config changes (and passes
  `mfd check`), so a settings page can change settings by writing the config file
  (keeping its comments and hand edits), and the file stays the one source of truth:
  `[act_now]` weights first, then plugins' sections. If the restart's second of downtime
  gets in the way there, apply edits live instead: swap the config in place, migrate
  plugins newly enabled, and restart only for `port` and `db_path`.
  The same form sets things up: `mfd serve` without a config opens it (still behind the
  token and same-site checks, writing only the config file), with the browser's
  autocomplete for what can be fetched as it's filled in (the org's teams once the org is
  given, like `mfd init`'s tab completion).
- **More signals for which branches are mine.** A PR of mine, my commits off the default
  branch and `my_branch_prefixes` decide it now; git-stack tracking it and being checked
  out in one of my worktrees could count too, if those miss some.
- **Host-qualified repo ids.** `repo` ids are GitHub `owner/name`, from a GitHub-only
  remote regex. A second code host (Bitbucket, GitLab) needs ids like
  `github.com/acme/widgets` so they can't collide, and `git` parsing any host. A shared
  base for code-host plugins (PR panels over a normalized PR) can wait for that second host.
- **Windows, on a real machine.** CI passes on Windows, but these need trying by hand:
  `py dev.py install` (the PATH change, the `mfd.cmd` shim, the hooks through `py -3`);
  `mfd cert --trust`, then Edge, Chrome and Firefox (which should read the Windows store)
  on `https://127.0.0.1`; `mfd serve` replacing its server on a config change, and
  ctrl-c stopping it (the server ignores ctrl-c and the parent ends it);
  `mfd token set` with Credential Manager; git-stack itself on Windows; and repo paths
  compared as `Path`s (case, `~`, separators), including those stored in `notes_ice`.
  On a Windows that isn't in English, `icacls` names SYSTEM and Administrators in its
  language, so `private.is_private` wouldn't recognize them and the token file would be
  warned about on every start: look their names up from their well-known SIDs
  (`LookupAccountSidW`) rather than matching English ones.
- **Skill stand-ins for missing notes fields.** The move-forward skill keeps some state
  outside the database until the notes can hold it: check-ins (`Checked`, `Asked-done`) are
  `decline` items with those reserved texts; the once-a-day ice question and the per-PR
  "offered" dates live in its gitignored `daily.json`; merge conditions apply to a whole
  entry, so a PR's URL goes in the condition text; `reviewer-map.md` is read only through
  `mfd slack import` and `mfd github import`, so the skill should set Slack ids with `mfd
  slack contact set` rather than editing the map. Give the notes real fields or tables for these, per-PR
  conditions included, and move the skill onto them.
- **Finished entries are never pruned by the dashboard.** Prune deletes only entries
  finished 90 days ago; the skill clears finished ones itself with `mfd notes remove`.
- **Stale JSON in `snapshots.payload`.** Core no longer writes it, but the last JSON each
  plugin stored stays there, unused once that plugin's migration has copied it. A plugin
  can't clear core's table, so this needs a core migration that runs only after every
  built-in plugin's copy has shipped, or it stays as harmless dead data.
- **Config sections from the registry.** `Config` lists a field per built-in plugin; it
  could build them from the registry, so adding a plugin needn't change core.
## Design for the remaining steps

### Data model

What's left of it:

- **Finding `me`.** `refresh` finds `me` among `person` entities by name. Like linking
  kinds, this could become a declaration on a kind model.
- **A second code host.** A GitLab plugin would publish its own kind of PR (one plugin per
  kind), and every consumer would read both. If that gets repetitive, a PR could become a
  contract several plugins publish to. That can wait for the second host.

### Upkeep

Each plugin prunes its own data daily (notes: old done todos and finished entries; slack:
old pings). Still to add: notes dropping old declines, and github dropping looked-up emails
no PR has asked about in `prune_days`.

### Risks

- **Notes migration loses information the parser doesn't model** (free-text lines,
  `Done` history formats). The importer keeps any line it doesn't understand as an
  entry-level note, and `export` lets you compare before deleting the files.
- **The skill and the dashboard drift.** The CLI's JSON output is the contract the
  move-forward skill is written against; change it deliberately, with the skill.
