# 08 — Explicit conventions instead of `getattr`

## Problem 12: conventions are implicit; magic panel numbers; cross-plugin `rank`

- `refresh_seconds` read with `getattr` (`refresh.py:101`): an author who names it
  `poll_seconds` gets no schedule and no error.
- `teams` table read with `getattr` (`config.py:105`).
- Token settings by structural typing (`keychain.py:19-26`; `cli.py:204-207` inspects
  `config_model.model_fields`): `mfd token set` silently skips a plugin whose field is
  `api_token_env`.
- `snapshot_model` doubles as the "collects" flag (`refresh.py:99,112,157`).
- Panel `priority` magic numbers with comments (`github/panels.py:259-529`,
  `notes/panels.py:409,452`, `jira/plugin.py:205,278`): an author has to read six plugins to
  pick 85.
- `Item.rank` is a within-panel order (`page.py:101`) but Act now uses it as a
  cross-plugin tie-breaker (`act_now.py:386`): jira's `rank=3000` beats everyone's.

**Before** for the three hooks (schedule, teams, token) and `collects`: they decide whether
a plugin refreshes and whether its token can be set. **After** for bands and `rank`
(cosmetic for the author; no stored data).

## PR 8a — schedule, teams, token, collects (before the cut)

- `Plugin.refresh_seconds(ctx) -> int | None`: default returns `ctx.config.refresh_seconds`
  when the model has that field, else `None`; built-ins keep the field. `refresh.py:95-102`
  calls it.
- `Plugin.teams_field: ClassVar[str | None] = None` (built-ins: `"teams"`): `Config`'s
  `_known_teams` reads it (needs no `ctx`, it's validation time).
- `Plugin.token_settings(ctx) -> TokenSettings | None`: default the structural check;
  `cli._token`/`_token_plugins` call it; `mfd token check` lists plugins that return one.
- `Plugin.collects` property = `snapshot_model is not None`, used by name in `refresh.py`
  and `server.py`; docstring says what it means.

**Tests.** A plugin with `poll_seconds` and an overridden `refresh_seconds()` is scheduled;
one without is not; `mfd token set` sees a plugin via `token_settings`. **Size.** S/M.

## PR 8b — bands and rank (after the cut)

- `api.Band` constants: `TODOS=0, REVIEWS=10, STACKS=20, FEEDBACK=30, CI=40, PUSH=50,
  STACKS_NO_PR=60, METADATA=70, REVIEWERS=80, NUDGES=85, MERGE=90, CLEANUP=100,
  TRANSITIONS=120, NEXT=130, PARKED=1000`, documented as "where in the day's check-in";
  built-ins use them (`+1` offsets stay as `Band.MERGE + 1`).
- `act_now.panels` drops `item.rank` from its sort key (`act_now.py:386`): score, pinned,
  then nomination order. `Item.rank` docstring: "within its panel".

**Tests.** Act now order with equal scores follows nomination order, not rank.
**Size.** S.

**Migration/compat.** None.

## Decisions

- `refresh_seconds` as a method with a field-reading default (**recommended**) vs
  requiring a `Schedule` object: the method keeps today's config shape and makes the
  convention visible in `Plugin`.

## Dependencies

04 (hook style). 09 depends on 8a (`teams_field`, `token_settings` replace the `getattr`s
that `Config` without typed fields can't do).
