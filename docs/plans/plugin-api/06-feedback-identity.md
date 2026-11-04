# 06 — Act now feedback keyed by a stable action id

## Problem 8: feedback is keyed by display text

`Nomination.key = (kind, id, signals.verb)` (`act_now.py:131-135`), stored as
`act_now_feedback.verb` (`db.py:153`). jira's verb embeds a configured status name
(`jira/plugin.py:197`: `f"Move to {to}"`), notes' embeds a count (`notes/panels.py:382-384`:
`f"Finish {len(quick)} quick todos"`). Rewording a verb, renaming a Jira status, or
finishing one of three todos silently drops the item's snooze, dismissal or pin. `mfd
snooze` duplicates github's verb constant (`snooze.py:10` vs `github/panels.py:45`), so
the two can drift. **Before**: the stored row's shape changes, and a third-party author
would otherwise learn the hard way that their verb is an identity.

## PR 6 — `Signals.task` (core migration V12)

**Changes.**
- `Signals.task: str` (`^[a-z][a-z0-9_]*$`), required for a nominated item: core logs (once
  a plugin) and drops a nomination without one. Named `task`, not `action`: `Item.actions`
  are buttons and `Feedback.action` is snooze, dismiss or pin, so `action` would have meant
  three things. `verb` stays display text. `Nomination.key = (kind, id, task)`; the
  feedback buttons' fields, the `also` list, `Item.pending` and `mfd snoozes` carry `task`
  instead of `verb`.
- Migration rebuilds `act_now_feedback` with `task` in place of `verb`, backfilling each
  verb's slug (lower-case, spaces → `_`), jira's "Move to …" and "Update the status" as
  `transition`, slack's pings as `ping`, notes' "Finish N quick todos" as `close_out`, and
  dropping rows without a verb; it **drops the `CHECK` on `action`** so later values
  (subplan 14's `opened`) need no rebuild.
- Built-ins' tasks: each fixed verb's slug, so existing feedback survives (github `review`,
  `reply`, `fix_ci`, `meet_pr_policy`, `restack_the_stack`, `resolve_conflicts`, `merge`;
  git `delete_the_branch`; jira's What next and rollout verbs); jira `transition`, slack
  `ping`, notes `close_out` for their dynamic verbs.
- `snooze.py` imports `REVIEW = "review"` from `api.kinds` beside `PULL_REQUEST` (it's about
  the family), as github does, removing the duplicate.
- README and `CHANGELOG.md`: `mfd snoozes` JSON field `verb` → `task`.

**Tests.** Backfill over a V11 database (`Review` becomes `review`, the dynamic verbs their
tasks, a row without a verb goes, `action` takes a new value); rewording a verb keeps a
snooze; a nomination without a task is dropped with a log line; `mfd snoozes` prints `task`.

**Risk.** Medium (stored data, skill contract). **Size.** M.

## Decisions

- Backfill: **slugify** (github's `Review` → `review` survives; dynamic verbs lapse) vs
  drop all feedback (simpler, loses the user's snoozes and pins).
- Require `task` on nominated items (**recommended**: a missing id is a bug the author
  should see) vs default it to the verb's slug (hides the bug until a reword).

## Dependencies

05 (kind names in the same table are rewritten there first; one rebuild sees final
values). 11 and 14 rely on the dropped `CHECK`.
