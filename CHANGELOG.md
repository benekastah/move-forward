# Changelog

What changed for anyone driving `mfd` from outside: its commands, their JSON, the config,
the HTTP endpoints and the page's links. Newest first. Each entry says what to do about it.

## Unreleased

### Entity kinds are named for their plugins; pull requests are `pull_request`

A plugin's own kinds now carry its name, and the PR family is spelled out. A family's kinds
(`pull_request`, `ticket`, `person`) are named for the family, whoever publishes them.

| Before | Now |
| --- | --- |
| `pr` | `pull_request` |
| `branch` | `git.branch` |
| `repo` | `git.repo` |
| `stack_branch` | `git_stack.stack_branch` |
| `entry` | `notes.entry` |
| `todo` | `notes.todo` |
| `iced` | `notes.iced` |
| `ticket`, `person` | unchanged |

Where you'll meet them:

- `mfd priority <entity> <level> --kind K` takes the new names (`--kind notes.todo`,
  `--kind git.branch`), and says "PR" for a pull request in its messages.
- `mfd notes ice --item <kind> <id>` takes `pull_request`, `ticket` or `notes.entry`.
- `mfd notes list`, `show` and `ices` JSON: a link's or iced item's `kind` is the new name.
- `mfd snoozes` JSON: `kind` is the new name.
- `/actions/act_now/*` and `/actions/notes/ice_item` take the new names in `kind`.
- `[act_now] snooze_days` keys: `pr = 7` is now `pull_request = 7`, `entry = 2` is
  `"notes.entry" = 2` (quoted, for the dot). The config refuses an old name, saying the new
  one.
- The page's item anchors are built from the kind: `#item-pr-…` is now
  `#item-pull_request-…`, `#item-entry-…` is `#item-notes-entry-…`. Old bookmarks to an
  item open the top of the page.

What the dashboard kept (priorities, Act now's snoozes, dismissals and pins, the notes'
links and what's on ice) is renamed by migrations when it next opens the database.
