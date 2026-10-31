# Entities

Plugins never read each other's config, snapshots or tables. What one plugin
knows that another needs goes through the entity graph: each plugin publishes
entities, each named by a `Ref` (a kind and an id), with a title, an optional
URL and state, the refs it points at, and attributes. Core builds the graph
from every enabled plugin's entities on each refresh and page load.

[plugins.md](plugins.md) draws who publishes and who reads each kind. This
file is the contract. Adding a kind or an attribute, or changing what one
means, is a deliberate change: update this file in the same commit.

When two plugins publish the same ref (say, a branch git and git_stack both
know), the graph merges them: the first plugin's title, URL and state win,
refs and attributes are combined, and the first plugin's attribute wins a
clash.

`Graph.related(ref, kind)` finds entities of `kind` one hop from `ref` in
either direction, or that share a `ticket` with it: a work entry naming
APP-1 is related to every PR whose branch, title or body mentions APP-1.

## Kinds

| Kind | Published by | id | state | refs |
| --- | --- | --- | --- | --- |
| `pr` | github | the PR's URL | `open`, `merged` or `closed` | `ticket` keys from its branch, title and body |
| `person` | github (only me, for now) | GitHub login | | |
| `repo` | git | GitHub `owner/name` | | |
| `branch` | git, git_stack | `<repo dir>\t<branch>` | | its `repo` |
| `ticket` | jira | the issue key, e.g. `APP-1` | the issue's status | |
| `entry` | notes | the entry's title | | the `pr` URLs and `ticket` keys it names |
| `iced_branch` | notes | `<repo dir>\t<branch>`, like `branch` | | |

`url_for(kind, id)` gives a URL for an entity that isn't in the graph: github
answers for `pr` (the id is the URL), jira for `ticket` (from its `site`).

## Attributes

### `pr`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `repo` | string | `owner/name` |
| `number` | int | |
| `head`, `base` | string | branch names |
| `author` | string or null | login |
| `draft` | bool | |
| `mine` | bool | I opened it |
| `changes_requested` | bool | a reviewer requested changes |
| `created_at` | ISO datetime | |
| `merged_at` | ISO datetime or null | |
| `requested_users` | list of logins | reviewers requested as people |
| `requested_teams` | list of strings | teams requested: a team's top-level name where `[github.teams]` maps it, else its GitHub slug |

On my open PRs only:

| Attribute | Type | Meaning |
| --- | --- | --- |
| `review_blockers` | list of strings | why reviewers shouldn't be nudged yet, e.g. `conflicts`; empty when the PR is in order |
| `last_review_activity` | ISO datetime or null | the latest review request, review or human comment |
| `review_stale` | bool | no review activity for `[github] stale_review_hours` |

### `person`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `me` | bool | the person using the dashboard |

### `repo`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `dir` | string | the main checkout's path |
| `default_branch` | string | |
| `my_dirs` | list of strings | directories I committed to within `[git] commit_history_days` |

### `branch`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `ahead`, `behind` | int | commits ahead of and behind its upstream |
| `gone` | bool | its upstream was deleted |
| `repo_dir` | string | the checkout it's in (git) |
| `sha` | string or null | the commit it points at, as of the last refresh (git) |
| `checked_out_in` | string or null | the worktree it's checked out in |

On branches git-stack tracks (git_stack):

| Attribute | Type | Meaning |
| --- | --- | --- |
| `stack_parent` | string | the branch it's built on |
| `stack_status` | string or null | git-stack's status, e.g. `up-to-date`, `needs-restack`, `merged` |

### `ticket`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `summary` | string | |
| `status` | string | |
| `status_category` | string | Jira's `new`, `indeterminate` or `done` |
| `type` | string | the issue type |

### `entry`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `merge_conditions` | list of strings | what must happen before its PRs merge |
| `open_todos` | list of strings | its open todos' text |

### `iced_branch`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `repo_dir` | string | the repo's directory, with `~` expanded |
| `name` | string | the branch's name |
| `stack` | string | the bottom branch of the iced stack it belongs to |
| `sha` | string or null | the commit it pointed at when iced |
