# 14 — Refresh subjects: opened items dim, refetch on focus, per-item refresh

## Problem 15: opened items vanish instead of refreshing

Opening a link of an Act now item posts `act_now/pending` (`render.py:122-142`,
`refresh.py:334-343`), which hides the item for up to 10 minutes (`act_now.py:62`,
`set_aside` at `:176-183`) until the plugin's `fetched_at` passes; a plugin button in Act
now does the same through `PENDING_FIELD` (`refresh.py:372,387`). On tab focus the script
POSTs `/refresh` for the whole plugin (`render.py:137-139`, `server.py:642-651`): github runs
all its searches because one PR was opened. There is no way to refresh one item. The
author approved: (a) on tab focus, refetch the items opened recently; (b) a per-item
refresh button; (c) opened items dimmed with "opened Nm ago" until their data changes.

**After** the cut (an additive hook), but designed now in the new hook style so the freeze
doesn't contradict it. github already has the cheap path: `client.fetch_prs`
(`github/client.py:210`) plus `placing.place`, used by `collect` after `changed`
(`github/plugin.py:85-95`).

## Design

```python
@dataclass(frozen=True)
class SubjectsRequest:
    ctx: Context[Any]
    refs: list[Ref]  # only kinds this plugin publishes; at most 50


@dataclass(frozen=True)
class SubjectsResult:
    updated: list[Ref]  # fetched and stored; the rest the tool didn't know


class Plugin:
    subjects_timeout_seconds: ClassVar[int] = 60

    def refresh_subjects(self, req: SubjectsRequest) -> SubjectsResult | None:
        return None  # unsupported: core refreshes the whole plugin instead
```

Core groups refs by the plugin that published each (05: a family's ref goes to the
plugin whose entity has the id, a plugin's own kind to that plugin), queues a subject job, and the refresh loop runs subject
jobs ahead of due full refreshes under `_refresh_lock`, so a tab focus doesn't wait on a
full collect; the hook runs with its timeout; `events(before, after)` runs as `_store`
does so a merged PR still notifies; `snapshots.fetched_at` is not touched (the full
refresh's meaning stays). A plugin returning `None`, raising or timing out falls back to
`request_refresh({name})`. Triggers: tab focus, the per-item button, an action's
`touched` refs (04), and `mfd refresh
--subject <kind> <id>` for the skill.

Opened state reuses the fingerprint lapse snoozes already have: an `opened` row with the
item's fingerprint dims the item until the fingerprint changes, or after `OPENED_FOR = 24h`.

## PR 14a — hook, endpoint, loop, fallback, github

- `refresh_subjects`, `SubjectsRequest/Result` in `plugin.py`/`api`.
- `App.request_refresh_subjects(refs)` and the loop change (`server.py:289-303`);
  `refresh.refresh_subjects(config, db, refs)` for the CLI and tests.
- `POST /refresh/subjects`, JSON `[{kind, id}]` validated with pydantic (≤ 50), same-site
  and token checks as every POST.
- github: PR URL → `PrId` (host/owner/name/number parse), `fetch_prs`, `placing.place` into
  `load()`, `store()`; `updated` = the PRs GitHub returned. jira as a follow-up
  (`client.tickets`, `jira/plugin.py:395-396`).

**Tests.** The hook gets only the refs of kinds the plugin publishes; a plugin without it
triggers a refresh of just that plugin; github merges one PR and keeps the rest of the
lists; a timed-out hook falls back; endpoint rejects 51 refs and another origin.
**Size.** L.

## PR 14b — opened state and UI (core migration: named, V14)

- Feedback action `pending` → `opened`: `UPDATE act_now_feedback SET action = 'opened'
  WHERE action = 'pending'` (no rebuild: 06 dropped the `CHECK`). `set_aside` no longer
  hides `opened`; `panels.build` sets `Item.opened_at` (core-set, replacing `pending`)
  when the main subject has an `opened` row with the same fingerprint and it's younger
  than 24 h; `PENDING` constant goes.
- Render: `class='item opened'` (dimmed) and a badge `opened 5m ago`; items carry
  `data-subject` (`{kind, id}` of the main subject) and `data-fingerprint`; `data-pending`
  and `Item.pending` removed (13 left it for here). `PENDING_FIELD` on plugin buttons →
  the same `opened` call keyed by `data-subject`.
- JS: on an outbound click, POST `act_now/opened` with subject and fingerprint and remember
  the subject in `sessionStorage` with a timestamp; on `window.focus`, POST
  `/refresh/subjects` with the subjects opened in the last 30 min (deduplicated, ≤ 50); a
  per-item `↻` link-action on every item with a subject posts the item's subjects. Dimmed
  items keep their place and number.
- `mfd snoozes` unaffected; README API table and the Act now section updated.

**Tests.** An opened item is dimmed; undimmed when its fingerprint changes; expired after
24 h; the migration converts rows; `test_refresh.py:296-325` rewritten for the new
behaviour.

**Migration/compat.** One trivial core migration. **Risk.** Medium (loop and lock
interplay; test the ordering of subject jobs against a due full refresh). **Size.** M.

## Decisions

- Opened cap: **24 h** (an item you opened and left shouldn't stay grey for weeks) vs none
  vs today's 10 minutes (defeats the point).
- Dimmed items keep their place (**recommended**: Act now's numbering stays stable) vs sink
  to the bottom of their panel.
- Subject jobs under the same refresh lock (**recommended**: `store()` must not race a full
  collect's `replace`) vs a separate lock with per-plugin serialization.

## Dependencies

04 (hook style), 05 (which plugin published a ref), 06 (no table rebuild), 13 (`Item.pending`
handover). After the cut.
