# 07 — Core builds qualified names; pages under `/<plugin>/`; `Response`

## Problem 7: plugins hand-build names core should own; page paths are global; sign-in pages bypass auth

- Action names: `"notes/todo_done"` (`notes/panels.py:51-64,306,318,425`),
  `"jira/transition"` (`jira/plugin.py:118,188`), `"git_stack/stack_clean"`
  (`git_stack/panels.py:120,185`), `"slack/connect"` (`slack/account.py:16-18`), git's
  helper (`git/panels.py:25`). A typo posts to a 404; a plugin can name another's action.
- Page paths are global and first-match (`refresh.py:472-475`): slack serves `/ping`,
  `/slack`, `/slack/connect`, `/slack/callback`, contacts; two plugins with `/ping` clash
  silently.
- **Security**: a path in `sign_in_pages` is served before the token check whatever it is
  (`server.py:554-559`), so a plugin can expose `/settings` or `/` unauthenticated.
- Separators are folklore: panel key `.` (`panels.py:36`), `KEPT` `:` (`plugin.py:72`,
  `refresh.py:277`), source `github.derived` (`github/plugin.py:237`, parsed at
  `render.py:438`), notification kind `.` (`notifications.py:77`); `Event.thing` is a
  global tag (`notifications.py:54`: github uses the URL, git `branch:<key>`), so two
  plugins' events about one URL merge into one notification.
- Handlers return only a string, so slack redirects with `location.replace` in JS
  (`slack/account.py:96-104`).

**Before**: names end up in users' bookmarks, Slack app manifests and stored data, and the
auth hole must close before any code you didn't write declares a sign-in page.

## PR 7a — actions, keys, sources, events

- `ctx.action(name, label, fields, **kw) -> Action` builds `"<plugin>/<name>"`. At
  `panels.build`, an `Action.name` that isn't `<enabled plugin>/<name>` or `act_now/…` is
  dropped and logged. Remove the hand-written prefixes (sites above).
- `ActionResult.keep(key)` on `ctx` → `{KEPT: f"{ctx.name}:{key}"}`.
- `ctx.record_part("derived", collector, store)` wraps `collect_into` with
  `<plugin>.<part>` (`github/plugin.py:236-250`); `render._plugin_of` keeps parsing.
- `Event.thing`: core prefixes `<plugin>:` when storing (`notifications.py:88-98`);
  `Event.anchor` built via `ctx.anchor(ref)`.

**Tests.** An action with a bare name is dropped and logged; `KEPT` round-trips; events of
two plugins about one URL are two notifications. **Size.** M.

## PR 7b — pages under the plugin's prefix, `Response`, sign-in scoped

- `pages()` keys are relative: `""` (index), `"ping"`, `"contacts"`, `"connect"`,
  `"callback"`; core serves `/<plugin>/<key>`. `/slack/callback` keeps its URL (the manifest
  and `oauth.py:118` build it; change to `ctx.page_url("callback")`); `/ping` →
  `/slack/ping`.
- `sign_in_pages` must name keys of `pages()`; core serves only `/<plugin>/<key>` without
  the token (closes `server.py:554`).
- Handlers return `str | Response(status=200, body="", headers={}, content_type=...)`;
  `Response.redirect(url)`. slack's `connect` returns a redirect.
- `server.RESERVED_PATHS` lists core's top-level segments (`focus`, `settings`, `login`,
  `notifications`, `refresh`, `layout`, `actions`, the static files); 09's name check uses
  it.
- `docs/plugins/slack.md`, README API table.

**Tests.** A plugin can't claim `settings` as a sign-in page; two plugins with a `ping`
page both serve; a redirect `Response` gives 303 with `Location`; the callback is still
served without a cookie. **Size.** M.

**Migration/compat.** No stored data. Bookmarks to `/ping?pr=…` break (one user). The Slack
app's redirect URL is unchanged.

**Risk.** Low/medium; the sign-in scoping is a behaviour change worth a line in the PR.

## Decisions

- `/<plugin>/…` (**recommended**: keeps `/slack/callback`, reads as today; safe once
  plugin names can't be core paths) vs `/p/<plugin>/…` (never collides with a future core
  path; breaks the manifest redirect and every page URL).
- `ctx.action` factory (**recommended**) vs core rewriting bare names at render: the
  factory makes the owner explicit when notes adds a button to github's item.

## Dependencies

03 (`ctx.name`), 04 (`PageRequest`, `ActionRequest`). 09 uses `RESERVED_PATHS`.
