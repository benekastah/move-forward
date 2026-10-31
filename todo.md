# To do

What's left of the plugin rearchitecture (`plugin_rearchitecture.md` has the design).
Steps 1–4 are done: core, github, git, jira, slack and notes are plugins. Each step below is
its own stacked branch, with core changes in a branch of their own ahead of the plugin
change that needs them (see `AGENTS.md`).

## 4b. git-stack plugin

- `[git_stack]`, no keys of its own. In `after_collect` it runs `git stack info` in each
  repo from git's `repo` entities, storing a `git_stack` snapshot part.
- Moves from git: the Stacks panel, the `stack_clean` action, and the git-stack calls in
  git's collector.
- Owns whole-stack icing: notes' `ice` stops shelling out to `git stack show-stack`, and
  git's "Put on ice" button stops posting to `notes/ice` (an item action instead).
- Publishes `stack_parent` (and status) on `branch` entities; `docs/entities.md` gains it.
- Core: plugins gain `requires` (e.g. `["git"]`), checked when the config loads:
  "[git_stack] needs [git]". Composition, not inheritance: no warning for git + github.

## 5. Notes in SQLite

- The notes tables (`notes_entries`, `notes_links`, `notes_todos`, `notes_todo_links`,
  `notes_conditions`, `notes_declined`, `notes_iced_stacks`, `notes_iced_branches`) as the
  notes plugin's migrations.
- `mfd notes import <dir>` / `export <dir>`; lines the importer doesn't understand are kept
  as entry-level notes.
- The `mfd notes ...` CLI (list, show, add-entry, set, link, todo add/done/undone/delete/
  link, condition, decline, ice, thaw), JSON with `--json`, working without the server.
- Todos addressed by id in `/actions/notes/...`; core stops knowing `notes/todo_done` by
  name and stops building the done todo with notes' `todo_actions` (an action result can
  carry redirect parameters instead).
- slack gets its own tables (`slack_contacts`, `slack_team_channels`, `slack_pings`) and
  github `github_people`; the reviewer-map import and core's `work_items`, `iced_stacks`,
  `reviewer_users`, `team_channels`, `pings` tables go (a core migration copies their rows).
- `[notes] dir` is dropped; core no longer imports markdown on every refresh.
- The daily notes prune asks the graph instead of calling the GitHub and Jira clients;
  needs a core lookup hook for PRs and tickets older than the snapshots.
- The move-forward skill's SKILL.md switches from editing the markdown to the CLI, in the
  skill's repository, the same week.

## 6. Layout and Act now

- `priority` on panels; the `layout` table (already migrated) with drag and drop on group
  and panel summaries, `POST /layout`, and Reset layout in the footer.
- Panel keys namespaced as `<plugin>.<panel>`.
- The `act_now` hook, `Item.signals`, the ranking with weights under `[act_now]`,
  diversity per plugin and per subject, merging across tools through the graph, and an
  explanation on every item.
- The `act_now_feedback` table: snooze, dismiss, pin.
- `BasePlugin.act_now` fallback from panels marked `actionable`.
- Focus mode follows the layout and gets an Act now category.

## 7. Worktrees

- A path in `[git] repos` that's a worktree of another listed repo is folded into it.
- Actions accept any worktree of a configured repo, not only the listed path.

## 8. Init and docs

- `mfd init` asks which plugins to set up, as one list answer checked against the registry
  (default all; notes always on), then each chosen plugin's `init_questions`.
- README sections per plugin; `docs/writing-a-plugin.md`.

## Follow-ups

- **Typed entity schemas.** Shared kinds as typed models in core, private kinds declared
  by their plugin and namespaced, typed reads for consumers, `docs/entities.md` checked
  against the models. `entity_schemas.md` has the plan.
- **Which branches are mine.** Signals for git, besides `my_branch_prefixes`: my commits
  ahead of the default branch (limited to recently active branches), a PR of mine from the
  branch in any state, git-stack tracking it, a reflog saying I created it from a local
  branch, being checked out in one of my worktrees. All commits ahead by others and no PR
  of mine means a review checkout: not mine.
- **Host-qualified repo ids** (`github.com/acme/widgets`) and git parsing any host, before
  a second code host. A shared code-host base for PR panels can wait for that host.
- **Windows.** The Jira token's macOS keychain fallback; the `.sh` scripts and git hooks;
  `shell` stopping subprocesses with POSIX process groups; `os.execv` in the reloader;
  repo paths compared as `Path`s (case, `~`, separators) and written into `on-ice.md`.
- **CI.** GitHub Actions running ruff, basedpyright and the tests, the tests on Linux,
  macOS and Windows (runners are free for this public repo).
