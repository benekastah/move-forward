# 05 — Kinds: one declaration, shared kinds, lifecycle, namespaced private kinds

Groups problems 1 and 2: both are "who owns the meaning of a kind", and the lifecycle flag
that fixes 2 lives in the same `KindSpec` and the same migration that fixes 1.

## Problem 1: kinds are global bare strings owned by whoever declares them first

`Plugin.kinds` (`plugin.py:108`); ownership by iteration order (`registry.py:140-147`).
Every consumer hard-codes `"pr"` (`jira/plugin.py:27`, `git/panels.py:17`,
`git_stack/panels.py:18`, `notes/panels.py:18`, `notes/covers.py:10`, `slack/pings.py:47`).
A GitLab plugin can't publish PRs: as `pr` it loses to github, as `mr` it's invisible to
git, jira, notes and slack. Kind strings are persisted: `priorities.kind` (`db.py:138-146`),
`act_now_feedback.subject_kind` (`db.py:149-163`), notes' ice and link tables
(`notes/actions.py:120,144`), `[act_now] snooze_days` keys (`act_now.py:101`). **Before**:
the day a third party ships a kind, its name is in users' databases.

## Problem 2: core knows tool states

`priority._DONE_STATES = {"merged", "closed", "done"}` (`priority.py:13`); `_finished`
special-cases `ticket.status_category` (`:50-55`); `forget_finished` names `pr`, `ticket`,
`entry`, `todo` (`:72,76`); `RESOLVED_KINDS` (`:29`) decides what `mfd priority` can name.
A Linear plugin's done issues keep their priority forever and `mfd priority ENG-12 1` says
"no entity the dashboard knows". **Before** (it's the other half of 1).

## PR 5a — `KindSpec` and `Entity.lifecycle` (no migration)

- `KindSpec(attrs: type[Model], linking: str | None | Literal[False] = False, hides=False,
  fetchable=False, gone_means_done=False, resolvable=False)`; `Plugin.kinds: dict[str,
  KindSpec]` replaces `kinds` + `linking` + `hides` (`plugin.py:108-119`).
  `linking_patterns` stays as the dynamic narrowing.
- `Entity.lifecycle: Literal["open", "done"] = "open"` beside the tool's `state`. github
  sets it by list (`github/plugin.py:301-309`); jira by `status_category == "done"`; notes'
  done todos aren't published, so `todo` and `entry` get `gone_means_done=True`; `branch`
  stays `False` (git's refresh may have failed, `priority.py:61-62`).
- `priority.py`: delete `_DONE_STATES`, `_finished`, `RESOLVED_KINDS`; `forget_finished`
  uses `lifecycle`, `fetchable` (what to look up) and `gone_means_done`; `resolve` tries
  the kinds marked `resolvable` (pr, ticket, entry, branch).
- `tests/test_entities_doc.py` reads `KindSpec`; `entities.md` gains a lifecycle column.

**Tests.** `forget_finished` driven by `lifecycle` with no kind names in the test; a
`gone_means_done` kind clears when missing, a plain one doesn't. **Size.** M.

## PR 5b — shared kinds owned by core (no migration)

- `api/kinds.py`: `PR`, `TICKET`, `BRANCH`, `REPO`, `PERSON`, each a `KindSpec` with its
  attribute model moved from the plugin (`github/kinds.py`, `jira/kinds.py`,
  `git/kinds.py`). A plugin publishes a shared kind by listing it in `publishes:
  ClassVar[frozenset[str]]`; several may. Core validates against the core model and sets
  `Entity.source` to the publishing plugin. Same ref from two publishers: first stands,
  logged (as now).
- Names stay `pr`, `ticket`, `branch`, `repo`, `person` (decision below).
- Consumers import `api.kinds.PR` and drop their local `PR = "pr"`.
- `docs/entities.md`: "Published by" becomes "Publishers (core-owned)" for these rows.

**Tests.** Two test plugins publishing `pr` both appear with their `source`; a plugin
publishing a shared kind it didn't list is dropped and logged. **Size.** M.

## PR 5c — private kinds prefixed by core (core migration V11 + notes migration)

- A kind declared in `kinds` that isn't shared is published as `<plugin>.<kind>`. Core
  prefixes at `build_graph`; a plugin makes refs to its own kinds with `ctx.ref(kind, id)`
  (and `ctx.kind(kind)` for the string); other plugins write the qualified string
  (`"git_stack.stack_branch"`, `"notes.entry"`, `"notes.iced"`, `"notes.todo"`), as they
  write `"pr"` today.
- Core migration: `UPDATE priorities SET kind = 'notes.entry' WHERE kind = 'entry'` and the
  same for `todo`, `iced`, `stack_branch`, in `priorities` and
  `act_now_feedback.subject_kind`. notes migration: `notes_ice_items.kind` and its link
  kinds for `entry` rows.
- **User config**: `[act_now] snooze_days` keyed by a private kind must be renamed
  (`entry` → `notes.entry`); a `Config` validator says so, like `MOVED_SETTINGS`
  (`config.py:33-42`). `mfd priority --kind todo` → `--kind notes.todo` (README row).
- Anchors for those kinds change (`page.anchor`); bookmarks to `#item-entry-…` break.
  `notifications.anchor` is transient.

**Tests.** A private kind is prefixed and `ctx.ref` agrees; migration over a V10 database
with a row of each kind; `snooze_days = { entry = 2 }` fails to load with the message.
**Risk.** Medium (stored data). **Size.** M.

## Decisions

- Kinds model: (a) namespace everything (`github.pr`): simple, but every consumer
  hard-codes github and a second code host is invisible; migrates every stored kind.
  (b) shared core kinds + first-declarer private kinds: multi-publisher works, private
  names still collide globally. (c) **Recommended**: shared core-owned kinds and
  core-prefixed private kinds, migrated now while one database exists.
- Rename `pr`/`ticket` to `code_review`/`work_item`: **no**; the names fit a GitLab MR
  or a Linear issue, and the rename migrates every row for vocabulary.
- `Entity.lifecycle` as a two-value field (**recommended**) vs a richer status enum; `gone`
  is core's observation, not the publisher's, so it's a `KindSpec` flag.

## Dependencies

04 (hook style; `EntitiesRequest` is where `ctx.ref` is handy). 06 depends on this (it
rebuilds `act_now_feedback` and should see the rewritten kinds). 10 extends `KindSpec`
with `fetched_attrs`.
