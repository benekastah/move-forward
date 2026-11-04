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

## PR 6 — `Signals.action` (core migration V12)

**Changes.**
- `Signals.action: str` (`^[a-z][a-z0-9_]*$`), required for a nominated item: core logs and
  drops a nomination without one. `verb` stays display text. `Nomination.key = (kind, id,
  action)`; `_member_fields`, the `also` list, `Item.pending` and `mfd snoozes` carry
  `action` instead of `verb`.
- Migration rebuilds `act_now_feedback` with `action_id` in place of `verb`, backfilling by
  slug of the verb (lower-case, non-alphanumerics → `_`), and **drops the `CHECK` on
  `action`** (`db.py:154`) so later values (subplan 14's `opened`) need no rebuild.
- Built-ins' ids: github `review`, `merge`, …, equal to the slug of each fixed verb so
  existing feedback survives; jira `transition`, `advance_rollout`, `finish_rollout`,
  `work_on_it`, `fix_the_bug`, `look_at_the_incident`, `chase_the_blockers`,
  `watch_the_rollout`; notes `close_out` (dynamic verb: existing notes snoozes lapse);
  git `delete_the_branch`.
- `snooze.py` imports `REVIEW_ACTION = "review"` from the place github defines it
  (`api.kinds` beside `PR`, since it's about the shared kind), removing the duplicate.
- README: `mfd snoozes` JSON field `verb` → `action`; the skill reads it (`cli.py:400-413`),
  so note the skill change.

**Migration/compat.** Core migration; the skill's `mfd snoozes` contract changes.

**Tests.** Backfill over a V11 database (a `Review` row becomes `review`); rewording a verb
keeps a snooze; a nomination without `action` is dropped with a log line; `mfd snoozes`
prints `action`.

**Risk.** Medium (stored data, skill contract). **Size.** M.

## Decisions

- Backfill: **slugify** (github's `Review` → `review` survives; dynamic verbs lapse) vs
  drop all feedback (simpler, loses the user's snoozes and pins).
- Require `action` on nominated items (**recommended**: a missing id is a bug the author
  should see) vs default it to the verb's slug (hides the bug until a reword).

## Dependencies

05 (kind names in the same table are rewritten there first; one rebuild sees final
values). 11 and 14 rely on the dropped `CHECK`.
