# 11 — Named migrations, Python migrations, prefix check

## Problem 13: migrations are index-counted SQL strings with no prefix check

`db.migrate` (`db.py:247-283`) counts list length in `schema_versions`: inserting or
reordering a migration silently applies the wrong one, and there's no name to say which
ran. No Python migrations (a data fix that needs a loop can't be written). Nothing stops a
plugin creating a table called `prs` next to github's. Built-ins' first migrations read
core's `snapshots.payload` and V1 tables (`github/tables.py:98-170`,
`jira/repository.py:38-57`, `git/repository.py:10`, `git_stack/repository.py:27`,
`slack/repository.py:44-52`), which core keeps for them (`db.py:80-84,130-136`).

**Before** for the format: changing a plugin's `migrations` list format after third parties
have shipped lists is a flag day; now it's six built-ins. The core-table coupling is
built-in-only and **deferred** (keep, and note that `V1_SCHEMA` and `snapshots.payload` are
frozen for it).

## PR 11 — `Migration(name, run)` and a ledger (core migration V13)

**Changes.**
- `Migration(name: str, run: str | Callable[[sqlite3.Connection], None])`;
  `Plugin.migrations: list[Migration]`; `api` exports it.
- `db.migrate` keeps a ledger `migrations(owner, name, applied_at)`; the new core
  migration creates it and backfills from `schema_versions` counts using each built-in's
  names, so the index→name switch is exact on existing databases. It refuses a list whose
  applied prefix doesn't match the ledger (renamed or reordered) and still fails when the
  ledger has names the code lacks ("upgrade the dashboard"). `schema_versions` stays as a
  count for one release, then goes.
- A Python `run` executes inside the same `BEGIN … COMMIT` as the version row.
- After each plugin migration, diff `sqlite_master` and refuse tables/indexes not prefixed
  `<name>_` (built-ins comply).
- Built-ins' lists converted in the same PR, with names like `0001_people`, `0002_prs`.

**Migration/compat.** Core migration; no data change. The automatic backup covers it.

**Tests.** Backfill on a V12 database; a renamed migration refused; an unprefixed table
refused; a Python migration runs in the transaction and rolls back on error.

**Risk.** Medium (it's the code that protects data). **Size.** M.

## Decisions

- Named migrations before the freeze: **yes**. Alternative: keep index counting and
  document "append only" — it's documented today and still fragile for an author who
  reorders during development.
- Prefix enforcement by diffing `sqlite_master` (**recommended**: works for SQL and Python
  runs) vs parsing SQL.

## Dependencies

06 (the `act_now_feedback` rebuild there is the last index-counted core migration touching
data; this one follows it). 14's `opened` migration is a named one.
