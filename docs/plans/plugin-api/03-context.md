# 03 — A fuller `Context`

## Problem 11: `Context` is thin

`Context` (`plugin.py:37-65`) has `config`, `email`, `teams`, `prune_days`, `now`, `db`, `kv`,
`dashboard_url`. No `name` (plugins repeat their own name in strings:
`f"{self.name}.{DERIVED}"`, `github/plugin.py:237`), no `log` (module loggers, so a
third-party log line isn't attributable to a plugin by name), no `data_dir` for a plugin
that needs a file (a cache, a clone). `KvRepository` has only `get`/`set`
(`repositories.py:55-70`), so plugins clear by writing `"[]"` (`github/plugin.py:116`) and
hand-roll JSON (`:115,121`, `git/plugin.py:64,148`, `jira/plugin.py:332`).

**Before inviting others**: small and additive, and every later subplan wants `ctx.name`
and `ctx.log` (01's throttled logging, 07's `ctx.action`).

## PR 3 — context additions

**Changes.**
- `Context` gains `name: str`, `log: logging.Logger` (`move_forward.plugins.<name>`),
  `data_dir: Path` (`<db_path.parent>/plugins/<name>`, created on first access and made
  private with `private.make_private`, as the token file is). Built in `registry.enabled`
  (`registry.py:121-130`).
- `KvRepository`: `delete(key)`, `keys(prefix="")`, `get_json(key, model: type[T]) -> T |
  None`, `set_json(key, value: BaseModel | list | dict)`.
- Built-ins: replace the JSON sites above; use `ctx.log` where a `ctx` is at hand (plugin
  modules keep module loggers for pure helpers).

**Migration/compat.** None. **Tests.** kv round trips and `delete`/`keys`; `data_dir`
created private; `ctx.log.name`. **Risk.** Low. **Size.** S/M.

## Decisions

- `data_dir` under the database's directory (**recommended**; it inherits the private
  permissions and backup story) vs `~/.cache`.

## Dependencies

02 (export the new names from `api`). Nothing depends on it strictly, but 04 and 07 use
`ctx.name`/`ctx.log`.
