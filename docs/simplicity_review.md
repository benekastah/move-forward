# Simplicity review

> A record of the code at `206461e`, not kept up to date: items done since aren't struck out
> here (the PRs that do them say which), and the roadmap's "First: simplify" step goes when
> they're done.

A review of move-forward for what could be removed, merged or moved, the roadmap's first
step. Read at `206461e` (main, 2026-11-09) by three parallel passes:

- the plugin API, from a plugin author's side;
- machinery the plugins duplicate that could move to core;
- user-facing features that overlap or cost more than they give.

The claims the recommendations rest on were re-checked against the code.

Sizes: **S** is under a day, **M** is a few PRs, **L** is a project. Line counts are
`wc -l` at that commit.

## Contents

- [A. Move repeated plugin machinery into core](#a-move-repeated-plugin-machinery-into-core)
- [B. Trim the plugin API](#b-trim-the-plugin-api)
- [C. Features that overlap: pick one](#c-features-that-overlap-pick-one)
- [D. Config and CLI that cost more than they give](#d-config-and-cli-that-cost-more-than-they-give)
- [E. Docs](#e-docs)
- [Worth keeping as is](#worth-keeping-as-is)
- [Recommended order](#recommended-order)

---

## A. Move repeated plugin machinery into core

The biggest wins: each copy below has already produced bugs.

### A1. Change detection: core owns "what moved"

**Today.** Six plugins each keep a fingerprint in kv and compare it on the next check:
mail (`STATUS`), vcard (`SIGNATURE`), git_stack (`FINGERPRINT`), github (`BRANCHES_SEEN`),
git (`FINGERPRINTS`) and jira (`CHECKED`). git and github also each keep a pending set and a
"full collect due" timer:

- git: `MOVED`, `LAST_FULL` (`plugins/git/plugin.py:87-107, 187-193`);
- github: `CHANGED`, `LAST_FULL`, `_full_due`, `_take_changed`, `_note_changed`
  (`plugins/github/plugin.py:145-213, 230-233`).

The pending sets exist because core asks `changed` right after a refresh and throws the
answer away (`server.py:201-224`), so each plugin has to remember for itself.

**Cost.** The two copies differ:

- github sets `LAST_FULL` before collecting; git sets it only after a collect that worked.
- Both drop what was pending when a collect fails.
- git returns `bool`, github `Changed`.
- mail and vcard may treat mail that arrives between collect and the post-refresh check as
  already seen (unverified race).

Bugs from these copies: #30/#31, #178's git 6 and github 1, and #192's loop (a failed
collect asking for a refresh at every check).

**Options.**

- **(a) M:** `changed` returns a fingerprint, or the subjects that changed. Core takes it
  before the collect starts, stores it, keeps what's pending until a successful `store`, and
  passes `collect` the subjects and whether it's a full collect (core knows
  `refresh_seconds`). About 9 kv keys and 70 lines go, with the bugs above. Raises
  `API_VERSION`.
- **(b) S:** core compares against a fingerprint taken when the refresh starts, rather than
  discarding the post-refresh check. Plugins keep their kv.

### A2. One way to report a problem that isn't a failure

**Today.** Four ad hoc ways:

- github `_poll_failed`: in memory, warns once while the reason lasts
  (`plugin.py:131, 260-266`).
- git `_clones_warned`: in memory, once per run, log only (`plugin.py:74, 115-123`).
- slack `LOOKUPS_FAILED`: kv plus a notice on Slack's page (#202).
- github `_lookup_emails` (`plugin.py:322-326`): `except Exception`, warns per login on
  every refresh and keeps nothing. This is #178's slack 9, still present in github.

Core already shows failures in the footer through `collect_into(ctx.part(...))`, but only
github's `derived` part uses it.

**Option (S–M).** `ctx.problem(key, text | None)`: core logs it when it changes, shows it in
the footer or the plugin's status, and clears it when set to None. It replaces all four, and
fixes github's per-login spam.

### A3. Tokens

**Today.**

- `token_env` and `keychain_service` fields, with near-identical docstrings, in jira
  (`config.py:34-39`), slack (`config.py:33-40`) and mail (`config.py:94, 145-150`).
- The "no token: run `mfd token set X`, or set $Y" message is copied in jira
  (`client.py:119-125`, raising `RuntimeError`) and mail (`client.py:59-64`, raising
  `SourceError`). jira's `fetch` runs in the daily prune, where a `RuntimeError` reads as
  "unknown" rather than "fix this".
- `settings.SECRET_LOCKED` (`settings.py:38-45`) still names the built-ins' locked keys in
  core, though `Plugin.locked_settings` exists and no built-in sets it. Its `token_env` and
  `keychain_service` entries are also redundant with `TOKEN_KEYS`.

**Options.**

- **S:** `ctx.token(account=None)`, raising `SourceError` with core's message, and a
  `TokenFields` base model in `api.keychain`.
- **S:** the built-ins set `locked_settings`, and `SECRET_LOCKED` shrinks to
  `{None: {"email"}}`. Add a test that every key a plugin's token settings read is locked.

### A4. Config examples written by hand up to five times

**Today.**

- `cli.py:46-180` (`EXAMPLE_CONFIG`) hand-writes every plugin's section, a near-copy of
  each `setup.py` `TEMPLATE`.
- `init.py:130` already builds the same thing from
  `plugin.init_example + plugin.init_template`.
- The copies have drifted: jira's `[jira.teams]` comment, and slack's `team_channel` and
  `keychain_service` comments.

AGENTS.md's "every config key in four places" is really five for a plugin, and it makes
core know every tool.

**Options.**

- **(a) S:** build `mfd init --example` from the plugins' templates (plus a flag for
  sections that start commented out). About 120 lines of core go.
- **(b) M:** generate the commented defaults from the models (each key's default and the
  first line of its docstring). Most `setup.py` `TEMPLATE`s go, and the rule becomes two
  places: the model and the doc page.

### A5. Smaller moves

| What | Where | Option | Size |
|---|---|---|---|
| git_stack's `config_file` copies git's `common_dir` | `git_stack/changes.py:30-45`, `git/changes.py:16-34` | `git_common_dir` in `api.shell` | S |
| Watchers hash `git.branch` entities into kv to see what git's refresh touched | git_stack `changes.fingerprint`, github `pushes.tips` | give `ChangedRequest` the previous graph | M |
| Two HTTP clients with different error mapping | jira `client.py:195-216`, slack `client.py:26-41` | one `api.http.request(...)` (already on the roadmap) | S |
| Only mail strips invisible and bidi characters from a stranger's text | mail `client.py:51-56, 374-412` | move `_shown` into `api.html`; core applies it to every rendered title | S |
| Two lookup caches (login→email, login→Slack id) with the same source precedence, retry and prune, each with its own reviewer-map import | `github/people.py`, `slack/repository.py:58-115` | an `api` helper for kept lookup caches; one `mfd import <dir>` | S–M |
| "Newly in a panel" notifications rebuilt by hand | git `events.py` and `graph_events`, jira `graph_events` | `Item.notify=(kind, text)`; core diffs consecutive builds | S–M |

---

## B. Trim the plugin API

**Census.** Plugin has 31 overridable hooks, 22 class variables and 26 request/result types,
and `move_forward.api` exports 80 names. A plugin that shows a panel of fetched items needs
about 13 concepts:

- `Plugin[C, S]` with 7 class variables;
- a `Strict` config, plus `init_template`/`init_example`;
- a migration and a repository;
- `collect`, `store` and `load`;
- a `KindSpec` and a `KindModel`;
- `entities`;
- `panels`, with `Panel`, `Item` and `Band`.

Used by no plugin:

- hooks `refresh_seconds` and `token_settings`;
- class variables `families`, `locked_settings`, `subjects_timeout_seconds` and
  `collect_timeout_seconds`;
- KindSpec flag `fetched_attrs`;
- `Collected(more=True)`.

Used by one plugin: `collect_after`, `refresh_subjects`, `relate`, `linking_patterns`,
`handle_for`, `kept_item`, `tokens`.

| # | Option | Why | Size |
|---|---|---|---|
| B1 | Remove `Collected(snapshot, more=True)`, `in_pieces`, `MAX_PIECES` and core's piece loop | No plugin has ever returned it (checked with `git log -S`); only `test_refresh.py` uses it | S (raises `API_VERSION`) |
| B2 | Call `url_for`/`handle_for`/`fetch` only on the plugin whose `kinds` include the kind; default `url_for` to the id for URL-id families | Every implementation starts with `if kind != X: return`; github's `url_for` is just `id if kind == PR` | S |
| B3 | One item-actions hook (keep `prepare_item_actions`, renamed) | git and git_stack override `item_actions`, notes and slack `prepare_item_actions`; an author must learn both | S |
| B4 | Derive `collects_after_graph` from overriding `collect_after`; drop the "refreshed with what it requires" rule | Only git_stack collects after the graph, and it both requires and watches git, so the rule never fires | S |
| B5 | Merge `events` and `graph_events` | Overlapping inputs; jira implements both | M |
| B6 | Remove unused machinery: `ctx.data_dir`/`data_root`, `KindSpec.fetched_attrs`, the timeout class variables (make them core constants), `linking` as a string (make it a `bool`), never-overridden hooks; flip `init_suggested`'s default to False (4 of 5 overrides return False); accept one return type from `changed` | Nothing uses them, or they're inverted | S |
| B7 | Merge the 26 request types into about four shapes (ctx; ctx+graph; ctx+kind+id; ctx+snapshot), keeping old names as aliases for a version | About 105 lines of `ctx, graph = req.ctx, req.graph`; costs reading, not bugs | M (optional) |
| B8 | `Band`: keep a few generic bands, or document it as the skill's order rather than an API | 12 of its 15 values are used by exactly one plugin, so core encodes each tool's panel position | M, or docs only |

Complex for a good reason: `hides` (it decouples git and git_stack from notes), families
and id rules (unique cross-tool ids), and `identify`/rekey (legacy ids, and unknown handles
in mentions).

---

## C. Features that overlap: pick one

These are product decisions: each removes code, but also something a user can see.

### C1. Importance: five mechanisms on two scales

**The five:**

- Priority ❕/❗/‼ (`priority.py`, `mfd priority`).
- Contact types (`[people] contact_types`, Set type and Clear type, `mfd contact` and
  `mfd contacts`, per address or `@domain`).
- vcard's `group_importance` and `group_types`.
- mail's `urgent_words` and `low_words`.
- Pin.

**The scales disagree.** Priority 0 means "not important" (`act_now.py:300`,
`PRIORITY_IMPORTANCE = {0: -1, 1: 1, 2: 2}`), while contact types and vcard groups use 0 as
"normal".

**Unused today.** Core's organisation handling (`people.py`, 285 lines;
`tests/test_people.py`, 515): key accounts, VIP badges and organisation-type inheritance.
No plugin publishes an `organisation` yet (`docs/entities.md:154`).

**Options.**

- **(a) S:** make the scales agree (priority 0 means normal, or rename it). **Done:** "Not
  important" (C2b) is gone, so a priority is 1 or 2 and no longer reads 0 as ranked down.
- **(b) M:** remove the organisation handling until a CRM plugin needs it. Nobody loses
  anything today.
- **(c) M:** drop contact types and set priority on people (a person is never done, so it
  persists). Lose the "customer" badge, domain-wide types and `[people]`.

### C2. Setting work aside: five ways

**The five:**

- Snooze: until a time, or until the item changes.
- Dismiss: until it changes.
- Put on ice: hides the subject and what it links, everywhere.
- Priority "Not important": ranks it down.
- "Opened": dims an item for a day.

**Evidence.** Dismiss is a Snooze with no end time: same table (`act_now_feedback`), same
"until it changes". An Act now item carries Snooze, Dismiss, Pin, Put on ice, three
importance buttons, Clear priority, a Tag field, Untags and Set type. `mfd snooze` takes only
PRs and keys a snooze by `head_oid` (`snooze.py:36`), while the button keys it by the item's
title, badges and details, so the two end under different conditions.

**Options.**

- **(a) S/M:** fold Dismiss into Snooze, as an "until it changes" length.
- **(b) S:** drop "Not important"; Dismiss or ice covers it. **Done.**
- **(c) S:** make `mfd snooze` take any entity and key it as the button does.

Ice earns its keep: it hides across panels, covers linked items and keeps a stack's commits.

**Decided: keep Snooze and Ice apart.** Folding them into one "Set aside" with a length
(until tomorrow, a week, until it changes, until I bring it back) was considered. They
differ on more than duration:

- Snooze sets aside one nudge (an item's task); Ice sets aside a piece of work and what it
  links, in every panel.
- Ice keeps a note, has Reopen, and keeps a stack's commits so deleted branches can be
  restored, which a timed set-aside would complicate.
- They're stored apart (core's `act_now_feedback`, notes' `notes_ice`), so merging them
  means a migration and a new owner.

Options (a)–(c) above still stand on their own.

### C3. Notifications restate Act now

**Today.** About 1,500 lines, off by default:

- core's `notifications.py` (212 lines), the browser script and stream, and the settings
  checkboxes;
- github `changes.py` (210 lines), jira `changes.py` (76), git and git_stack `events.py`,
  jira's `graph_events`;
- about 830 lines of tests.

Most of the 21 kinds restate a panel: `pr_mergeable` is Ready to merge, `ci_failed` is CI
failures, `merge_conflict` is Merge conflicts, `branch_deletable` is "joined Cleanup", and
jira's `closeable` is its transitions panel. jira's comments exist only for notifications:
the `jira_issue_comments` table (migration 0004), the `/myself` call and the mention
parsing.

**Options.**

- **(a) L:** core works out generic events ("new in Act now", "new in panel X"), and the
  per-plugin hooks and most kinds go. Lost: `ci_recovered`, `pr_closed`, `author_replied`,
  and jira's comment and mention kinds.
- **(b) M:** keep only `dashboard.source_failing` and "new in Act now".
- **(c) S:** at least drop jira's comment kinds and their table.

### C4. The settings page is a partial second editor for the config file

**Today.** `settings.py` (368 lines), `tomledit.py` (105), tests (about 310) and the locked
settings list. That list is a per-plugin security list every new plugin must get right. The
page can't edit tables, `db_path`, `port`, or hosts and credentials. Its one irreplaceable
job is the "Allow in this browser" button for notifications.

**Options.**

- **(a) M:** remove it. Settings go in the file, which `mfd serve` already reloads, and the
  notification checkboxes and permission button move to a small block on the dashboard.
- **(b) S/M:** shrink it to Act now's settings and notifications.

### C5. Tags

**Today.** `tags.py` (290 lines), `tests/test_tags.py` (381), the `tags_from_text` flag, the
Tag field and Untag on every item, `mfd tag`, pruning tags from gone work, and long README
and mail-doc paragraphs. The only plugin that reads tags is mail (`mail/panels.py:110-117`),
to raise a thread whose subject names a tag something open carries. Elsewhere tags are
badges.

**Options.**

- **(a) M:** keep tool labels as badges; drop hand tagging and `#tag` parsing. Mail stops
  ranking by tag.
- **(b) M/L:** remove tags entirely.

### C6. Slack sign-in (OAuth)

**Today.** About 800 lines:

- `slack/oauth.py` (327 lines), `account.py` (134), `scopes.py` and `manifest.json`;
- the `/slack`, `/slack/connect` and `/slack/callback` pages, and 339 lines of tests;
- core's `sign_in_pages` exemption from the token check.

The only Slack call is `users.lookupByEmail` (`slack/client.py:43`), which a pasted token
does as well. The roadmap says the sign-in has never been tried with a real Slack app.
`manifest.json` still lists an `https://localhost` redirect, though HTTPS mode is gone.

**Options.**

- **(a) M:** remove it and keep the pasted token. Core's `sign_in_pages` could go too.
- **(b) S:** freeze it: no more work on it until a real app has been tried. At least fix the
  manifest.

### C7. Focus mode mostly earns its keep

About 200 lines of core (`render.py:734-827`, `panels.py:138-195`, the server, and the
`kept`/`kept_item` hook). It's also a third surface under "an item offers the same wherever
it's shown". The Act now walk-through and the todos split are the useful parts.

**Option (S):** drop its All and per-group walks.

---

## D. Config and CLI that cost more than they give

Each config key costs four hand-kept copies (see A4).

| What | Evidence | Option | Size |
|---|---|---|---|
| `[jira.teams]` | Only validated; "unused yet" in `jira/setup.py:20` and its docstring | Remove | S |
| github `reviewer_history_days` and git `commit_history_days` | Both mean "how far back local commits count", both default to 180 | One key, in `[git]` | S |
| `refresh_on_load_seconds` | `server.py:297-309`: any page load refreshes every plugin older than 60 s, overriding github's rate-limit-minded 300 s and the plugins' change detection | Remove (rely on change detection, ↻ and Refresh), or scale it per plugin, e.g. `max(60, refresh_seconds / 5)` | S |
| Knobs few would touch: github's review-size thresholds, `split_pr_suggest_lines`, `merge_queue_eviction_days`, `stale_draft_days`; Act now's seven weights, `snooze_until_hour`, `snooze_days`; git_stack's and vcard's `refresh_seconds` (backstops behind watching); git's `status_seconds`; `db_backups` | Each is a key in four places | Make them constants; keep the ones actually changed | S each |
| Mail accounts inheriting from `[mail]` | About 270 lines of config logic (`MailConfig._mailbox`, `_takes_auth_servers`) | Require each `[mail.<name>]` to be complete | M |
| Slack ping destinations from five sources (`[slack.teams]`, by hand, the import, the `team_channel` guess, the lookup) | Precedence rules across all five | Keep `[slack.teams]` plus by hand; make the import a one-off | S/M |
| Seven `mfd notes` bookkeeping commands (`decline`, `checked`, `asked-done`, `offered`, `offers`, `asked`, `last-asked`) with their own tables | The page never reads them; they're memory for the work-notes skill | One `mfd notes remember <key> [--on]` and `recall` | M |
| Three imports from the markdown skill (`mfd notes import`, `mfd github import`, `mfd slack import`), and notes export, which loses links and order | One-offs from the move to SQLite | One `mfd import <dir>`, or remove once migrated | M |
| `mfd contacts` (contact types) and `mfd slack contacts` (Slack ids) | The same name for different things | Rename one | S |

---

## E. Docs

- **AGENTS.md (303 lines).**
  - Move its process sections ("Reviewing a PR", "A full review", about 90 lines) to
    `docs/review.md`.
  - Drop rules that tests or ruff already enforce (encoding, import isolation), keeping
    only a pointer.
- **README.**
  - Its API sign-in section describes the HMAC handshake and cookies; that belongs in
    `auth.py`'s docstring.
  - People describes the unused organisation and VIP behaviour (see C1).
  - Tags carries about 15 lines of edge rules (see C5).
- **`docs/writing-a-plugin.md` (516 lines)** is the longest doc. Check it against `graph.md`
  and `entities.md` for overlap. B's changes would shorten it most.

---

## Worth keeping as is

- **Ice:** hides across panels, covers linked work, keeps a stack's commits.
- **Merge conditions.**
- **The Act now ranking itself:** only its knobs are over-exposed (D).
- **Mail's sender verification.**
- **Families and id rules.**
- **Focus mode:** with C7's trim at most.
- **Copies that differ for a good reason:** mail's and vcard's separate person ids (joined by
  email), git's and git_stack's iced-item actions, jira's minute-level `changed`, git's
  watcher.

---

## Recommended order

1. **Quick, safe removals (S PRs, a day or two).** No user-visible loss:
   - B1, B2, B3, B4 and B6;
   - A4a: build the example from the plugins;
   - A3: tokens, and locked settings in the plugins;
   - D: `[jira.teams]`, the duplicate history keys, `refresh_on_load_seconds`;
   - C6b: fix Slack's manifest.
2. **Structural fixes that stop repeat bugs:**
   - A1: core owns change detection;
   - A2: `ctx.problem`.

   Each is an API change landed in core first, then one PR per plugin.
3. **Product decisions (yours).** Each removes hundreds of lines, and something you can see:
   - C3: notifications;
   - C4: the settings page;
   - C5: tags;
   - C6a: Slack OAuth;
   - C1b/c: organisations and contact types;
   - C2a: Dismiss;
   - D's rarely touched knobs;
   - mail account inheritance;
   - the `mfd notes` bookkeeping commands.
