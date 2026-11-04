# 02 — `move_forward.api` and an API version

## Problem 4: no public API module, no version

Built-ins import from 15 core modules (`domain`, `graph`, `notifications`, `strict`, `db`,
`shell`, `plugin`, `page`, `asking`, `render`, `localtime`, `markdown`, `repositories`,
`act_now`, `keychain`, `https`; `grep -rh "^from \.\.\." src/move_forward/plugins`). An
author can't tell whether `render.e`, `act_now.due_pressure` (`jira/plugin.py:12`),
`KvRepository` or `shell.git` are theirs to use, and core can't tell which of its names it
may rename. There is no `api_version`, so a plugin built against last month's hooks loads,
and fails at the first call.

**Before inviting others**: the surface must be named before anyone codes against it, and
the version check must exist before the first incompatible change after that.

## PR A — the `api` module (early in the stack)

**Goal.** One module with `__all__` listing exactly what a plugin may use; built-ins import
only from it, enforced by ruff.

**Changes.**
- `src/move_forward/api/__init__.py` re-exports: `Plugin`, `Context`, `ActionError`,
  `ActionResult`, `SourceError`, `KEPT`, the handler types; `Entity`, `Ref`, `Graph`,
  `attrs`; `Model`, `Strict`; `Panel`, `Item`, `Action`, `Link`, `Signals`, `Effort`, `age`,
  `anchor`; `Event`; `Asker`, `InitSetup` and the `asking` validators built-ins use;
  `Database`, `Params`, `KvRepository`; `act_now.due_pressure`; `localtime.local`/`day`;
  `keychain.token`/`read`/`write`; `markdown.cell`/`tables`. Grouped submodules for the
  rest: `api.html` (`render.page`, `e`, `buttons`, `linkable`), `api.shell` (`run`, `git`,
  `CommandError`, `DEFAULT_TIMEOUT_SECONDS`), `api.http` (the `https` client).
- Rewrite every `from ...x import y` under `plugins/` to `from ...api import y`
  (mechanical; `git grep -l '^from \.\.\.' src/move_forward/plugins`).
- `pyproject.toml`: `[tool.ruff.lint.flake8-tidy-imports.banned-api]` for each core module
  with the message "plugins import from move_forward.api"; `TID` in `extend-select`;
  `per-file-ignores` so core itself is exempt.
- `docs/writing-a-plugin.md`: an "Imports" section; `AGENTS.md`: the rule. Later subplans
  export their new types from `api` (request objects, `KindSpec`, `Response`, …).

**Migration/compat.** None. **Tests.** Every name in `api.__all__` resolves; a test walks
`plugins/` for non-`api` core imports so the rule survives a ruff config change.
**Risk.** Low; large mechanical diff. **Size.** L (import lines).

## PR B — `api_version` and the freeze (last before the cut line)

**Goal.** Declare the API frozen, and refuse plugins built against another version.

**Changes.**
- `api.API_VERSION = 1`; `Plugin.api_version: ClassVar[int]` required. `check_names` (by
  then `check_plugins`, subplan 09) disables a mismatching plugin with an error line from
  `mfd check` and in the page footer, rather than raising at import.
- `docs/writing-a-plugin.md` rewritten for a separate package: layout, `pyproject` with
  the entry point, the `api` surface, the testing harness, migrations, families and a
  plugin's own kinds, reserved names, what counts as a breaking change (an `API_VERSION`
  bump).
- `docs/api-changes.md`: the changelog going forward (what each subplan moved).
- `plan.md`: drop what the stack finished ("Class variables to the top" rides along in
  subplan 04).

**Migration/compat.** None. **Tests.** A plugin with `api_version = 0` is disabled with the
message; the built-ins all declare the current one. **Risk.** Low. **Size.** M (docs).

## Decisions

- Flat `api` namespace plus three grouped submodules (`html`, `shell`, `http`):
  **recommended** over one flat module (sixty names) or one submodule per core module
  (recreates the problem).

## Dependencies

PR A: none; do it right after 01 so every later subplan adds to `api` instead of scattering
imports. PR B: after 01–12, since it freezes what they define.
