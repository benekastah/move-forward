# Plugin API changes

What changed in `move_forward.api` for a plugin's author, newest first. A plugin declares
the version it's built against (`api_version = API_VERSION`); the dashboard leaves out one
built against another version, and says so in `mfd check` and the page's footer.

A change that could break a plugin built against the current version raises
`API_VERSION`: a name leaving `api`, a hook's request losing or retyping a field, a
`Plugin` attribute changing meaning, a stored format a plugin reads changing. Adding a
name, a hook, a request field or an optional `KindSpec` flag doesn't.

## Version 1

The first frozen version. Everything below came before it, while only the built-ins used
the API; it's listed so a plugin written against an earlier checkout can catch up.

- **Imports.** Core is `move_forward.api` (with `api.html`, `api.shell`, `api.http`,
  `api.localtime`, `api.keychain`, `api.kinds` and `api.ids`) and nothing else.
- **Hooks take one request object** (`PanelsRequest(ctx, snapshot, graph)` and so on).
  `ItemActionsRequest` says whose item it is and where it's shown; `ActionRequest` reads a
  required field with `req.field(name)`.
- **A failing hook** is logged and isolated; `collect` has a timeout
  (`collect_timeout_seconds`), and may collect in pieces (`Collected(snapshot, more)`).
- **`Context`** has `name`, `log`, `data_dir`, `kept(key)`, `part(name)` and
  `page_url(key)`; its kv has `delete`, `keys`, `get_json` and `set_json`.
- **Kinds** are declared with `KindSpec` (`linking`, `hides`, `fetchable`,
  `gone_means_done`, `resolvable`, `fetched_attrs`) on a `KindModel`, which rejects unknown
  attributes. A plugin's own kinds are named for it (`git.branch`); what more than one tool
  may represent is a family core models (`pull_request`, `ticket`, `person` in
  `api.kinds`), whose ids follow its rule (URLs; `<host>/<login>`) and whose `handle` is
  what text names it by. An entity says `lifecycle` (`open` or `done`).
- **Fetched entities** must say `lifecycle` and fit their kind's fetched model.
- **Act now**: a nominated item needs `Signals.task`, a stable id its snoozes and pins are
  kept by.
- **Buttons** post only to an action a plugin declares in `actions()`; others are left
  out. **Pages** are keyed relative to the plugin and served at `/<plugin>/<key>`; a page
  may return a `Response`; `sign_in_pages` names keys of `pages()`.
- **Conventions are hooks**: `refresh_seconds(ctx)`, `token_settings(config)`,
  `teams_field`, `collects`.
- **Config**: a plugin's section is `config.section(name)`.
- **Packaging**: other packages' plugins load from the `move_forward.plugins` entry point
  group; names in `RESERVED_NAMES` are refused.
- **Migrations** are named `Migration(name, run)`s, SQL or Python, kept by name; a plugin's
  tables are prefixed with its name.
- **Tests**: `move_forward.testing.Harness`.
