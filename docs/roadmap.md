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
6. **Reviewer availability** (from any tool that knows it, once people have it: see Plugins
   without core changes, step 4): before suggesting a ping, the reviewer's status (out of
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
6. **Importance where it means something.** Say whether every panel sorts by it, since only two
   do now, not What next or Feedback.
7. **Focus mode's missing buttons.** Panel-wide buttons (Delete all, Clean) don't show there,
   nor do those of an item that focus mode splits into parts (an entry's Put on ice).
10. **Account on the settings page.** A Sign out everywhere button (`mfd logout`), and where
    each plugin's token is found (`mfd token check`). Setting or rotating a token stays in
    the terminal.

### github

Each button runs `gh`, checks its fields (a PR on `host`, a login's form), and returns the PR
as `touched`, so it's fetched again at once.

12. **Merge.** With a confirm, wherever the PR is listed (and in focus mode). What a repo allows
    comes from GitHub, not config: `isMergeQueueEnabled`, `autoMergeAllowed`, the allowed methods,
    `viewerDefaultMergeMethod` and `deleteBranchOnMerge`. github fetches those and offers:
    - On **Ready to merge**:
      - **Add to queue** with a queue on: `PUT repos/{o}/{r}/pulls/{n}/merge-async` with
        `merge_action=merge_queue` and no method (that parameter is unverified; check it first).
      - **Merge** otherwise: the same endpoint with the method and the head commit shown, so a
        push since the confirm refuses it. A PR in a linked stack can't merge any other way (and
        merging it merges the PRs below it, which the confirm says); try it on an unstacked PR
        first.
    - On **Ready for review**, and Fix before review's PRs waiting only on checks: **Merge when
      ready**, where auto-merge is allowed and wanted: `gh pr merge --auto` with the method and
      `--match-head-commit`, so a push after the confirm isn't merged unseen. Then a "merges when
      ready" badge and **Cancel auto-merge**. Not on a PR in a linked stack, which `gh pr merge`
      refuses.
    - Nothing anywhere else.

    Only real preferences are settings, in `[github.merge]`, with `[github.merge.repos."owner/name"]`
    overriding per repo (a table, as `[slack.teams]` is, since each repo sets several keys):
    `method`, `auto_merge`, and `delete_branch`, the repo's `deleteBranchOnMerge` unless set.
    A branch with PRs based on it is never deleted, by the dashboard or by GitHub: with
    `deleteBranchOnMerge` on, Merge retargets those PRs to the base first. Never `--admin`.
    `method`, e.g. `"squash"`, is a preference for when several are allowed: a repo allowing just
    one uses that, and one that doesn't allow the preferred method uses the one GitHub picks for
    me (`viewerDefaultMergeMethod`: my last used, else the repo's default), so one setting can
    serve every repo. The confirm says which method it will use.
15. **A base merge isn't an answer.** Comments count as answered once they're older than my last
    push, by the head commit's date, so merging the base into a branch (Update branch, or by hand)
    clears them with nothing said. Read past merge commits: it needs their parents fetched. Then
    Update branch's confirm no longer needs to warn of it.
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

24. **Size a todo, and say why something's on ice, from the CLI.** `todo add --size` sizes a
    new one; nothing sizes an existing one but the page's Quick and Big, and nothing changes
    an ice note but its Replace note, so the work-notes skill can't do either.
25. **Edit an entry.** What the CLI can do to one: `blocked-on` (its badge can't be cleared from
    the page), status and where (not shown at all), `link`, and `remove`.
26. **Merge conditions.** Held's "held: …" has no way out but `mfd notes condition remove`. Add
    and remove them on the PR's items.
27. **Start a todo from nothing.** The docs call notes alone a todo list, but a todo needs a PR
    or ticket to start from on the page. A New entry button on the panel (`mfd notes add-entry`).
28. **Undo a tick on the dashboard.** Focus mode keeps a ticked todo to untick; the dashboard loses
    it. Keep recently done todos, ticked, for a while.
29. **The same todo buttons.** In focus mode, each of an entry's several todos repeats its
    entry's Add todo (the page shows it once).

### slack


## Plugins without core changes

Adding a plugin should never mean changing core. It already doesn't for a tool that fits what
core models and how (Linear or Shortcut publish tickets); what forces a core change today is
anything new in shape, and a second code host (see "A second code host" under Code hosts:
`repo` ids and git's remotes are GitHub's). The tools in view: CRMs (Salesforce, HubSpot),
support (Zendesk, Intercom, Front), on-call and incidents (PagerDuty, incident.io), errors
(Sentry), a calendar (meetings to prepare for), and comments in documents (Google Docs,
Confluence, Notion, Figma). In order:

1. **A test that keeps it true.** An example plugin outside the package, built against
   `move_forward.api` alone and found through its entry point, run in CI with the test
   harness plugin authors get (`move_forward.testing`). Each step below extends it as it
   lands, so it shows what a plugin can do without core, and a core change that would break
   one fails it.
2. **Families a plugin can declare.** `kinds.FAMILIES` is a closed list (pull request,
   ticket, person), so every shared concept is a core edit. Let a plugin, or a small package
   several share, declare a family (its model, id rule, label, what it carries when fetched
   on demand, and any task ids its consumers share, as `REVIEW` is for PRs), with core only
   enforcing it, and move the three there are onto the same path so it stays tested. Two
   publishers that declare one family differently is an error at load, naming both.
3. **The families most of those tools need.** An *organisation* (a CRM account, a support
   tool's organisation, an email domain), a *conversation* (mail, support tickets, chats),
   and an *incident*. Each with the attributes a consumer ranks by, and a person pointing at
   their organisation. Contact types' `@domain` labels are an organisation already, and
   mail's threads a conversation.
4. **Who someone is, across tools.** A person with several identifiers (addresses, each
   tool's handle), not one email; `me` on core's `Person`, not just github's; how far an
   identity can be trusted, since an address in a header proves nothing (#97 publishes a
   sender only when the receiving server verified them; a family-level `verified` would let
   any plugin say so, and core trust only that); and availability, which a calendar, an
   on-call schedule or a Slack status can each give, so Slack feature 6 (Reviewer
   availability) reads it from any of them rather than Slack alone.
5. **What `Signals` can't say yet.** It has `due`, `waiting_since` and `importance` (where a
   plugin maps its tool's priority). Missing: a breach apart from `due` (a support SLA, an
   incident's acknowledgement), stakes apart from importance (a customer's tier, a deal's
   size), and who's waiting as people (`person` refs) rather than names, so their contact
   type and availability count.
6. **An HTTP toolkit in `api`.** Most of those tools have no CLI, and there are no new
   dependencies, so each plugin would write its own JSON requests, paging, retries with
   backoff, rate-limit waits and timeouts over `api.http.opener`. Do it once, with OAuth's
   token refresh (slack does its own today) beside `sign_in_pages`, and API tokens through
   `api.keychain` as now.
7. **Richer buttons.** Several fields on one form (today one text field and one drop-down),
   a text area (replying to a ticket), and a date (snooze until, follow up on).
8. **Customer data.** A kind can say it's sensitive, and `mfd dump`, logs, notifications and
   exports leave out or redact its entities' text; `prune` drops them once they're done.

Table-shaped settings (a support plugin's views, a CRM's tiers) belong to "First-run setup
in the browser", below, which already plans tables on the settings page.

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
- **Approval as a field.** slack tells an approved PR by the text `already approved` in its
  `review_blockers`, so a second code host has to say it the same way. A core `approved`
  attribute on the `pull_request` family would make it part of the contract, not the wording.

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
