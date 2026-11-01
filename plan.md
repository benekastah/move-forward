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

### 6b. Act now

- After living with the first version: `importance` (jira's escalations), `waiting_since`
  and `due` in the signals and the score, and merging nominations across tools through
  the graph ("Merge PR 330, then close APP-12").

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

### 10. Refresh in smaller steps

Each plugin refreshes on its own `refresh_seconds` now. What's left:

- **Change signals instead of webhooks.** Webhooks would need GitHub, Jira and Slack to
  reach `127.0.0.1`, i.e. a public tunnel: another service, and an opening onto the
  machine. Cheap signals that something changed get most of the benefit, so the heavy
  fetches can run rarely:
  - github: poll the notifications API (built for it: `X-Poll-Interval`, and a 304 when
    nothing's new is cheap); a notification about one of my PRs refreshes github at once.
  - git: watch `.git/HEAD` and the refs for changes, and refresh when they move.
  - jira: search only for tickets `updated` since the last check.
  - slack: Socket Mode, once the app exists (step 9): events come over a connection the
    dashboard opens itself.

## Follow-ups

- **Refresh a plugin by hand, and show when it last did.** Each plugin's group on the
  page gets a refresh button that refreshes just that plugin (and those built on it, as
  the server's loop does), and its `<summary>` shows when the plugin last refreshed, in
  local time (with its error, if the last one failed).
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
- **Icing and git-stack groups.** Putting a stack on ice walks the stack's parents only;
  git_stack doesn't publish groups, so a group's other stacks aren't iced with it.

## Design for the remaining steps

### Act now

The first version is built (see the README's Panels and `act_now.py`): plugins nominate
items with `Signals`, core ranks them, and Snooze, Dismiss and Pin are kept in
`act_now_feedback`. What follows is the design the later version (step 6b's last bullet)
fills in: `importance`, `waiting_since` and `due`, and merging across tools.

#### What makes something worth doing now

Three kinds of item earn a place, matching how people triage by hand:

- **Quick wins**: done in minutes. Reply to a thread, approve a small PR, tick a todo,
  send a follow-up email.
- **Close-outs**: the last step that finishes something bigger. Merge an approved PR,
  move a ticket to Closed, send the signed contract, a work entry's only open todo.
- **Important work**: matters more than its size. An escalated incident, a reviewer who
  is blocked on you, a deal that closes this week.

On top of those, two adjusters:

- **Someone is waiting.** A review request, a question addressed to me, a customer reply.
  Waiting time makes it more urgent, and blocking a person beats blocking a process.
- **It will go stale or get worse.** A deadline, a PR drifting into conflicts, a meeting
  tomorrow that needs prep, a lead going cold.

And one filter: **it must be doable now.** Something blocked on others, on ice, a draft
I'm not ready to show, or waiting for CI is not an action, however important.

#### The shared vocabulary

Each plugin already knows these things about its own items; it only needs to say them in
common terms. `Item` gains an optional `signals` field:

```python
class Signals(Model):
    effort: Literal["minutes", "hour", "day", "unknown"] = "unknown"
    closes: str | None = None
    """What doing it finishes, e.g. "APP-12" or "PR 330"; None if it only moves things along."""
    importance: int = 0
    """0 normal, 1 high, 2 critical. Plugin-specific inputs map onto this (escalation
    level, priority, deal size)."""
    waiting: list[str] = []
    """Who is waiting on me for it: logins, names, "customer"."""
    waiting_since: datetime | None = None
    due: datetime | None = None
    """A deadline, or when it will go stale (a lead's follow-up date, a review SLA)."""
    blocked: bool = False
    """Not doable now: waiting on someone else, on ice, CI pending."""
    verb: str = ""
    """What to do, as a short imperative: "Reply to 2 threads", "Merge", "Send the quote"."""
```

A plugin fills in what it knows and leaves the rest at the defaults. The first version
(step 6b) has only `effort`, `closes`, `waiting`, `blocked` and `verb`; `importance`,
`waiting_since` and `due` come after, once the first version's ranking has been lived with. Nothing in it is
specific to software: "effort", "closes", "who's waiting" and "due" mean the same for a
PR, a ticket, an email thread or a sales opportunity.

#### Examples

| Plugin | Item | effort | closes | importance | waiting | due | verb |
| --- | --- | --- | --- | --- | --- | --- | --- |
| github | Review request, +12 lines | minutes | | 0 | author | request age + SLA | Review |
| github | My PR approved, green | minutes | the PR (and its ticket, via the graph) | 0 | | | Merge |
| github | 2 unresolved threads on my PR | minutes | | 0 | reviewers | | Reply to 2 threads |
| github | CI failing on my PR | hour | | 0 | | | Fix CI |
| jira | Ticket In Review, all PRs merged | minutes | the ticket | 0 | | | Move to Closed |
| jira | Escalated incident | unknown | | 2 | reporter | | Look at the incident |
| notes | Entry's only open todo, `[quick]` | minutes | the entry | 0 | | | the todo's text |
| notes | Todo whose PRs all merged | minutes | | 0 | | | Tick it off |
| git | Branch merged, still local | minutes | | 0 | | | Delete the branch |
| slack | Reviewer quiet for 2 days | minutes | | 0 | | | Bump the review |
| crm (sales) | Opportunity, close date Friday, quote not sent | hour | the opportunity | 1 | buyer | Friday | Send the quote |
| email (sales) | Customer reply unanswered for 26h | minutes | | 1 | customer | reply SLA | Reply |
| calendar | Call tomorrow, no prep notes | hour | | 0 | attendees | the call | Prep for the call |

Effort can come from the tool (story points, a `[quick]` marker, a PR's size), from
simple rules (approve, merge, reply and delete are minutes), or stay unknown.

#### Ranking

A score per item, then a diverse top list:

```
score = 3 * closes?                        # finishing beats starting
      + 2 * (effort == minutes) + 1 * (effort == hour)
      + 3 * importance
      + 2 * waiting? + log2(1 + days waiting)
      + due_pressure(due)                  # 0 far away, rising to 4 when overdue
      - 100 * blocked                      # out
```

The weights are a starting point, in config under `[act_now]`, not code. Like every
config key, they go in the model, the README, `mfd init`'s rendered config and
`mfd init --example`:

```toml
[act_now]
size = 7                    # items shown
per_plugin = 3              # so one busy tool can't fill the list
weights = { closes = 3, minutes = 2, hour = 1, importance = 3, waiting = 2, due = 1 }
```

Then:

- **Diversity.** At most `per_plugin` items from one plugin, and at most one per subject:
  if a PR shows up as "Reply to threads" and "Fix CI", the higher one wins and the other
  is mentioned on it ("also: CI failing").
- **Merging across tools through the graph.** "Merge PR 330" and "Move APP-12 to Closed"
  are one close-out when the PR is the ticket's last open one. Act now shows them as one
  item, "Merge PR 330, then close APP-12", with both actions.
- **Explanations.** Every item says why it's there, from the signals that scored: "quick,
  closes APP-12, Alice waiting 2 days". This is what makes the ranking trustworthy and
  tunable.

#### Open questions

- **How much to trust tool-provided importance.** Jira priority is often stale; an
  escalation label is more telling. Per-plugin mapping into 0–2 keeps that judgment in
  the plugin.
- **Effort for unknown items.** Treat unknown as "hour" for scoring, or keep them out of
  the quick-wins slot only? Probably the latter.
- **Time of day.** Quick wins at the end of the day, important work in the morning? An
  easy later addition: a weight profile per part of the day.
- **Teams.** "Someone is waiting" could weigh people on my teams more. That needs the
  `person` entities to know team membership, which github already fetches.

### Data model

What's left of it:

- **Units of work in Act now.** Work isn't one kind every plugin must know, it's whatever
  linking kinds the enabled plugins publish (see `docs/graph.md`), so Act now merges
  nominations through them (a ticket, its PR and its entry's todo become one item).
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
