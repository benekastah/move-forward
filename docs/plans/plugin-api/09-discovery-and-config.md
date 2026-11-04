# 09 — Discovery by entry points, dynamic config sections, reserved names, order

## Problem 5: plugins are hard-coded in core; reserved names aren't

`registry.PLUGINS` (`registry.py:28-35`); a typed field per section in `Config`
(`config.py:74-79`); `settings.py:133,141`, `init.py:88-90`, `cli.py:196-207` iterate the
list; `check_names` reserves only `core` and `act_now` (`registry.py:50`) and runs at import
(`:54`). A separate package can't be loaded at all. A plugin named `act` (group key,
`panels.py:19`), `dashboard` (`notifications.py:26`), `settings` (a page), `serve` (a CLI
command) or `teams` (a top-level config key) collides silently. Ordering by list position
decides kind ownership (gone in 05), `me()` (gone in 04), `url_for` first answer, page
first match (gone in 07) and graph order. `enabled()` is called on every request and loop
tick (`server.py:146,152,171,188,225,348`), so discovery must be cached.

**Before**: this is what "installed as separate packages" means.

## PR 9a — `Config` without typed plugin fields

- Remove `github/git/git_stack/jira/slack/notes` from `Config`; `extra="allow"` at the top
  level only, with a `model_validator(mode="after")` that validates each extra key against
  the discovered plugin's `config_model` and rejects an unknown section naming the known
  plugins. `Config.sections: dict[str, BaseModel]`; `Config.section(name)`. `always_on`
  plugins get a default section when absent.
- `_known_teams` uses `Plugin.teams_field` (08); `_requirements` and
  `_known_notifications` read sections by name.
- `settings.py`, `init.py`, `cli.py`, `registry.py`, `tests/conftest.py` read sections by
  name; `Config.model_validate({... "github": {...}})` keeps working unchanged.

**Tests.** Unknown section error names the plugins; a section failing its model reports
the field; `settings` page still lists each enabled section. **Size.** L.

## PR 9b — entry points, reserved names, order

- Built-ins stay a list in core (`registry.BUILTINS`), so a source checkout run without
  installing, or a venv whose metadata is stale, still has them. Other packages' plugins
  come from `[project.entry-points."move_forward.plugins"]`. `registry.discover()` loads
  both once (cached), instantiates, runs `check_plugins`; a broken entry point is an error
  line from `mfd check` and a disabled plugin, not an import error; an entry point naming
  a built-in is refused.
- Order: alphabetical by name; tests depending on graph order adjusted.
- `RESERVED_NAMES` in one place: `core`, `act_now`, `act`, `dashboard`, `notifications`,
  `plugins`, `static`, every top-level `Config` field, every core CLI command
  (`cli.py:129-195`), every `server.RESERVED_PATHS` segment (07). `check_plugins` refuses
  them.
- `tests/test_entities_doc.py` and `conftest.git_polls` read `registry.BUILTINS`, so a
  third-party plugin in the dev venv doesn't change core's tests.
- Docs: "Registering it" → "Packaging it" (`pyproject` snippet, entry point); README says
  how to install one (`uv tool install move-forward --with my-plugin`).

**Tests.** A fake entry point (monkeypatched `importlib.metadata.entry_points`) is
discovered and enabled by its section; a plugin named `settings` is refused with the reason;
order is deterministic; a plugin whose import raises is reported and the rest load.
**Size.** M.

**Migration/compat.** Config files unchanged. Editable installs (`uv sync`, `pip install
-e`) write `entry_points.txt`; a stale venv shows "no plugins" from `mfd check`.

**Risk.** Medium (config loading everywhere).

## Decisions

- **Built-ins listed in core, entry points for other packages** vs entry points for every
  plugin (a checkout without an install, or with stale metadata, has no plugins at all) vs
  an explicit `plugins = ["pkg:Class"]` list in the config (works without packaging, one
  more thing to get wrong).
- Order alphabetical (**recommended**: deterministic for third parties) vs built-ins first
  in today's list order.

## Dependencies

07 (`RESERVED_PATHS`), 08 (`teams_field`, `token_settings`). 12 builds on
`discover()`/`BUILTINS`; 02 PR B adds the `api_version` check to `check_plugins`.
