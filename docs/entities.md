# Entities

Plugins never read each other's config, snapshots or tables. What one plugin
knows that another needs goes through the entity graph: each plugin publishes
entities, each named by a `Ref` (a kind and an id), with a title, an optional
URL and state, the refs it points at, and attributes. Core builds the graph
from every enabled plugin's entities on each refresh and page load.

[graph.md](graph.md) explains how the graph is built and read, and
[plugins.md](plugins.md) draws who publishes and who reads each kind. This
file is the contract. Adding a kind or an attribute, or changing what one
means, is a deliberate change: update this file in the same commit.

Each kind has one publisher: the plugin that declares it (the first, should two
declare one). An entity of a kind its plugin doesn't own, or a second entity of
the same ref, is left out of the graph and logged.

Some kinds are **linking**: their entities stand for a unit of work, and link
what's about it. `ticket` (jira) and `entry` (notes) are. `Graph.related(ref,
kind)` finds entities of `kind` one hop from `ref` in either direction, or
linked with it by a linking entity: a work entry naming APP-1 is related to
every PR whose branch, title or body mentions APP-1. A linking kind with a
pattern for its ids (`ticket`'s: a key in a project jira has seen a ticket
in, e.g. `APP-12`, so `UTF-8` isn't one) is found in other entities'
`mentions`, so they point at it without their plugin knowing the kind; see
[graph.md](graph.md#related-entities).

## Kinds

| Kind | Published by | id | state | refs |
| --- | --- | --- | --- | --- |
| `pr` | github | the PR's URL | `open`, `merged` or `closed` | the `ticket` keys its branch, title and body mention |
| `person` | github: me, and everyone it knows an email for | GitHub login | | |
| `repo` | git | GitHub `owner/name` | | |
| `branch` | git | `<repo dir>\t<branch>` | | its `repo` |
| `stack_branch` | git_stack | its `branch`'s id | | its `branch`, which git may have deleted |
| `ticket` (linking) | jira | the issue key, e.g. `APP-1` | the issue's status | |
| `entry` (linking) | notes | the entry's title | | the `pr` URLs and `ticket` keys linked to it and its open todos, and the `ticket` keys its title mentions |
| `iced` (hiding) | notes | the act of icing's id | | what it puts on ice (a stack's `branch`es, or a `pr`, `ticket` or `entry`), and what else that covers: the branches' `pr`s and `stack_branch`es, an entry's refs |

`url_for(kind, id)` gives a URL for an entity that isn't in the graph: github
answers for `pr` (the id is the URL), jira for `ticket` (from its `site`).

## Fetched on demand

`Graph.fetch(kind, ids)` asks the plugin that owns a kind for entities by id, now, even ones
long gone from its snapshot; it's slow, so it's for rare work like the daily prune. What a
fetched entity carries:

| Kind | Fetched by | state | Attributes |
| --- | --- | --- | --- |
| `pr` | github, with `gh pr view` | `open`, `merged` or `closed` | `closed_at`: ISO datetime or null, when it was merged or closed |
| `ticket` | jira, open or resolved; it remembers their projects | the ticket's status | `resolved_at`: ISO datetime, or null while open |

## Attributes

### `pr`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `repo` | string | `owner/name` |
| `number` | int | |
| `head`, `base` | string | branch names |
| `head_oid` | string or null | the commit the head branch is at on GitHub, as of github's last refresh |
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
| `email` | string or null | their commit email, imported or looked up from commits |

### `repo`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `dir` | string | the main checkout's path |
| `default_branch` | string | |
| `default_refs` | list of strings | the default branch on each remote (`origin/main`, `upstream/main`), or the local one when no remote has it, as refs `git log` takes in `dir` |
| `my_dirs` | list of strings | directories I committed to within `[git] commit_history_days` |

### `branch`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `ahead`, `behind` | int | commits ahead of and behind its upstream |
| `gone` | bool | its upstream was deleted |
| `repo_dir` | string | the checkout it's in (git) |
| `name` | string | the branch's name (git) |
| `sha` | string or null | the commit it points at, as of the last refresh (git) |
| `checked_out_in` | string or null | the worktree it's checked out in |

### `stack_branch`

A branch git-stack tracks, including one git no longer has.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `repo_dir`, `name` | string | its checkout and name, as git's `branch` has them |
| `stack_parent` | string | the branch it's built on |
| `stack_status` | string or null | git-stack's status, e.g. `up-to-date`, `needs-restack`, `merged` |
| `stack_group` | string or null | on a stack's bottom branch, the group its stack is in when the group has other stacks; git-stack's `restack`, `sync` and `push` act on the whole group |

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
| `merge_conditions` | object of lists of strings | what must happen before each of its PRs merges, by the PR's URL; a PR without conditions isn't in it |
| `open_todos` | list of strings | its open todos' text |

### `iced`

One act of icing, e.g. a stack. Core leaves items about whatever it hides off the page (see
[graph.md](graph.md#hidden-entities)).

| Attribute | Type | Meaning |
| --- | --- | --- |
| `iced_on` | string | ISO date |
| `note` | string or null | why it's on ice |
| `shas` | object | the commit each branch it put on ice pointed at then, by the branch's id |
