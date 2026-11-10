# Roadmap

What's left to build in move-forward. Only what's still to do lives here: when something
is done, remove it in the same change, and delete this file once it's empty. The code,
`AGENTS.md` and the rest of `docs/` are the reference for what's built.

## Goal

Every external tool is a plugin, so someone with a different set of tools (say, a
salesperson on a CRM and a calendar) can run the dashboard with only the plugins that fit,
and new tools can be added without touching the others. Plugins interact only through a
small shared entity graph.

## First: finish simplifying

The simplicity pass ([simplicity_review.md](simplicity_review.md)) is done but for the last of
its [plugin API trims](simplicity_review.md#b-trim-the-plugin-api): B7, widened from the
review's request shapes to fewer request types and fewer hooks to learn, as a stack of PRs.

- **Hooks removed:**
  - `refresh_subjects`: ↻ calls `collect` with its subjects;
  - `kept_item`: core keeps the focused item in memory across a button, so a todo just
    ticked shows as it is now, its box ticked, with its buttons but those that can't act
    from the remembered copy.
- **Hooks merged:**
  - `init_suggested` becomes a class variable;
  - `url_for` and `handle_for` become one hook, answering both for an entity by id;
  - `identity` goes: core reads who I am from the `person` a plugin publishes marked as me;
  - `act_now` goes, if items can say they're candidates (with their `signals`) in place of
    the four plugins' overrides;
  - `init_shown` folds into what `init_questions` returns;
  - `store` and `load` become one repository the plugin gives, if that spares a plugin
    keeping state between the two calls; otherwise they stay.

Then a further look at what's left: the hooks, and the class variables (about 21).

## Next, in this order

1. **[Buttons and consistency](#buttons-and-consistency)**, below.
2. **Gmail links that open the message**: see [mail](#mail), below.
3. **Read the address book where it lives**: see [Contacts](#contacts), below.

## Slack as a source of work

Slack becomes a source of work, not just a place pings go, through the token given slack
(`mfd token set slack`), a user token with each feature's scopes.

### First, try it with a real token

Before building features on it, check, with a real user token:

- how `search.messages` finds messages that @-mention me, and the rate limits an internal
  app gets on `conversations.history` and `conversations.replies`.

### How every feature works

- Each feature names the user scopes it needs, and `/slack/contacts` (and `mfd check`) names
  the scope the token lacks instead of failing in the log.
- Each is a setting in `[slack]`: those that read are on by default once there's a token
  (`mentions`, `threads`, `saved`, `discussions`, `recognize_pings`, `availability`,
  `reactions`, `rebump_after_changes`); those that send or change something are off by
  default (`summary`, with where it goes and an optional schedule, and `focus_status`).
  Like every config key, each goes in its two places.
- Messages are kept as ids, who, when, the channel and a short snippet only, in kept
  tables pruned after `prune_days`. Nothing is ever sent without a click.
- Slack is polled, on slack's own `refresh_seconds`, with `changed` checking cheaply in
  between. Socket Mode would need an app of its own, with an app-level token.

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

2. **Set aside from anywhere.** An item only Act now shows (nominated with no panel item) has
   nowhere to come back from but `mfd unsnooze`.
3. **An empty panel with a field opens.** A panel with no items is drawn collapsed, so a
   panel-wide field (notes' New todo) is hidden just when it's wanted: draw such a panel open.
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

12. **The rest of Merge.** Ready to merge has Merge and Add to queue, and Ready for review has
    Merge when ready and Cancel auto-merge. Still to do:
    - Merge when ready on Fix before review's PRs waiting only on checks.
    - Per-repo settings, `[github.merge.repos."owner/name"]`: `method`, `auto_merge`, and
      `delete_branch` (the repo's `deleteBranchOnMerge` unless set). A branch with PRs based on
      it is never deleted: with `deleteBranchOnMerge` on, Merge retargets those PRs first.
15. **A base merge isn't an answer.** Comments count as answered once they're older than my last
    push, by the head commit's date, so merging the base into a branch (Update branch, or by hand)
    clears them with nothing said. Read past merge commits: it needs their parents fetched. Then
    Update branch's confirm no longer needs to warn of it.

Approving stays on GitHub: a review should come from having read the PR there.

### git and git_stack

20. **Restack on a conflict.** Stacks' `needs-restack` items have Restack; github's merge
    conflicts that say restacking fixes them don't yet. It stays local: `sync` and `push`
    force-push (with lease), which the rule against force-pushing from a button forbids unless
    that's changed.

### notes

24. **Size a todo, and say why something's on ice, from the CLI.** `todo add --size` sizes a
    new one; nothing sizes an existing one but the page's Quick and Big, and nothing changes
    an ice note but its Replace note, so the work-notes skill can't do either.
25. **Edit an entry.** What the CLI can do to one: `blocked-on` (its badge can't be cleared from
    the page), status and where (not shown at all), `link`, and `remove`.
26. **Merge conditions.** Held's "held: …" has no way out but `mfd notes condition remove`. Add
    and remove them on the PR's items.

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
3. **The families most of those tools need.** A *conversation* (mail, support tickets,
   chats) and an *incident*, each with the attributes a consumer ranks by; mail's threads are
   a conversation already. An *organisation* family (a CRM's account, a support tool's
   organisation) that people point at, whose type or importance counts for its people's items,
   once a plugin publishes one: contact types' `@domain` labels are the closest today.
4. **Who someone is, across tools.** A person with several identifiers (addresses, each
   tool's handle), not one email; `me` on core's `Person`, not just github's; how far an
   identity can be trusted, since an address in a header proves nothing (mail publishes a
   sender only when the receiving server verified them; a family-level `verified` would let
   any plugin say so, and core join and trust only that: a person's contact type spreads to
   everyone with their email, and github's reviewer emails come from commits, which anyone
   can author as someone else); and availability, which a calendar, an on-call schedule or a
   Slack status can each give, so Slack feature 6 (Reviewer availability) reads it from any
   of them rather than Slack alone.
5. **What `Signals` can't say yet.** It has `due`, `waiting_since` and `importance` (where a
   plugin maps its tool's priority). Missing: a breach apart from `due` (a support SLA, an
   incident's acknowledgement), stakes apart from importance (a customer's tier, a deal's
   size), and who's waiting as people (`person` refs) rather than names, so their contact
   type and availability count.
6. **An HTTP toolkit in `api`.** Most of those tools have no CLI, and there are no new
   dependencies, so each plugin would write its own paging, retries with backoff and
   rate-limit waits over `api.http.request` (which sends one request, with its timeout,
   and raises one `RequestError` when there's no answer). Do it once, with OAuth's
   sign-in and token refresh, and API tokens through `api.keychain` as now. A sign-in needs
   the page its redirect returns to served without the dashboard's token, which browsers
   don't send on a redirect from another site, so that page checks OAuth's single-use
   `state` itself.
7. **Richer buttons.** Several fields on one form (today one text field and one drop-down),
   a text area (replying to a ticket), and a date (snooze until, follow up on).
8. **Customer data.** A kind can say it's sensitive, and `mfd dump`, logs, notifications and
   exports leave out or redact its entities' text; `prune` drops them once they're done.
9. **More than one login of a plugin**: see [Accounts](#accounts), below.

Table-shaped settings (a support plugin's views, a CRM's tiers) belong to "First-run setup
in the browser", below, which already plans tables on the settings page.

### Accounts

A plugin reads one login: github many repos but one account, jira one site. One that can read
several does it itself (mail does: see its page), as accounts in its own section, so it keeps one
set of tables, pages and actions, and settles what two of them both see the way its tool needs
(the same conversation in two mailboxes is one item). Core gives each account its token:
`Plugin.tokens` names them, and `mfd token set <plugin>.<account>` stores one.

- **github**: a personal and a work login (on github.com, or an Enterprise host). `gh` keeps
  one active account per host, so each call would take its account's token (`gh auth token
  --user <login>`) as `GH_TOKEN`.
- **jira**: a second site, each with its own token. Ticket keys can look alike across sites,
  so each site's projects say which site a bare key is on.

### mail

- **Gmail links that open the message.** The `message_url` `mfd init` suggests for Gmail,
  `https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}`, doesn't
  open the message (seen on a personal Gmail). Find what does: the Message-ID's encoding in
  the fragment (`message_url` percent-encodes all but `@`, so `+`, `=` and `/` change), its
  angle brackets, `u/<address>` against `u/0`, or a link by Gmail's own ids (`X-GM-MSGID`,
  `X-GM-THRID`, which IMAP gives), then fix the suggestion and `GMAIL_MESSAGE_URL`.
- **Read Gmail without a password that can do more.** IMAP signs in to Gmail with an app
  password or OAuth's `https://mail.google.com/` scope, and either can send and delete mail;
  mail only promises, in its own code, to read headers. Gmail's API has a scope Google enforces
  as read-only, `gmail.metadata` (headers and labels, never bodies), which fits mail exactly. A
  Gmail account would read through the API, signing in with OAuth; other servers
  stay on IMAP. The cost: `gmail.metadata` is a restricted scope, so a personal Google Cloud
  project with an OAuth client and myself as a test user, whose sign-in Google lets last about
  a week while the app is in testing (or the app goes through Google's verification).
- **A forged reply on its own, if it's ever abused.** A stranger's reply takes on the priority,
  pin and tags set on its conversation, a known limitation (see
  [plugins/mail.md](plugins/mail.md)). If that's ever abused, give a message whose sender the
  server didn't verify a conversation of its own, but only where senders can be verified at
  all (Gmail, or `auth_servers` set): elsewhere no sender is, and every conversation would
  split. That stops forged `From`s, not a stranger with a verified domain.

### Contacts

- **Read the address book where it lives.** A plugin publishing a `person` for each address
  in my contacts, titled by their name, with the groups they're in, and the `importance` or
  `contact_type` their groups give (a group's importance and type set in its section), so the
  people who matter rank first wherever they show up; a type I set (Set type) still beats
  theirs. It replaces the vcard plugin, removed because it read `.vcf` files from local paths,
  which only stayed current through a sync tool (vdirsyncer), while my contacts are in Google
  and Apple Contacts. Each a source of its own, as accounts in one section or a plugin each:
  - **Google Contacts** through the People API, with the read-only `contacts.readonly` scope.
    The same setup cost as Gmail's API (see [Read Gmail without a password that can do
    more](#mail)): a personal Google Cloud project with an OAuth client and myself as a test
    user, whose sign-in lasts about a week while the app is in testing; one project and
    sign-in could serve both.
  - **Apple Contacts** through iCloud's CardDAV, with an app-specific password, from any
    system (CardDAV gives vCards: vcard's parser reads them, `git show
    38b2bb7:src/move_forward/plugins/vcard/client.py`); or on macOS
    through the Contacts framework, without a password but on that one system, and reached
    from Python only through a new dependency (PyObjC) or a helper outside it.

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
  per part of the day, which could also rank personal mail down at 10am and work mail down
  at 10pm, with mail's account (`[mail.home]`, `[mail.work]`) saying which it is); whether
  someone waiting on my team should weigh more (the `person` entities would need team
  membership, which github already fetches); whether unknown
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

- **Move the skill onto the notes.** Per-PR merge conditions are in the notes now
  (`condition add --pr`), and check-ins, offers, declines and once-a-day questions are
  remembered by key (`mfd notes remember` and `recall`, keyed as
  [plugins/notes.md](plugins/notes.md) says); the skill still keeps them its old way until
  it's changed to use them. Its `reviewer-map.md` is read by nothing now, so it should set
  Slack ids with `mfd slack contact set` rather than editing the map.
- **Keep the skill and the dashboard in step.** The CLI's JSON output is the contract the
  skill is written against: change it deliberately, with the skill. The skill's own rule
  against cleaning while something's on ice no longer matches the dashboard, which cleans
  regardless (`git stack clean` never restacks or pushes).

### Windows, on a real machine

CI passes on Windows, but these need trying by hand: `py dev.py install` (the PATH
change, the `mfd.cmd` shim, the hooks through `py -3`);
`mfd serve` replacing its server on a config change, and ctrl-c stopping it;
`mfd token set` with Credential Manager; Slack's Connect; git stack itself; git watching its
repos while you work (the watcher holds handles on the watched directories, and Windows can
refuse to delete or rename a directory with handles open in it: `git worktree remove`, a
checkout that deletes a top-level directory, renaming a repo's folder); and repo paths
compared as `Path`s (case, `~`, separators), including those stored in `notes_ice`. On a
Windows that isn't in English, `icacls` names SYSTEM and Administrators in its language,
so `private.is_private` wouldn't recognize them and the token file would be warned about
on every start: look their names up from their well-known SIDs (`LookupAccountSidW`)
rather than matching English ones.
