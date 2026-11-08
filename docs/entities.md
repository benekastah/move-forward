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

A plugin's own kinds are named for it, `<plugin>.<name>` (`git.branch`,
`notes.entry`), so only it publishes them. A family's kind (`pull_request`, `ticket`,
`person`; see below) can have several publishers: its entities' refs name the family, and
each says which plugin's kind it is in `kind` (`github.pull_request`). An entity of a kind
its plugin doesn't declare, or a second entity of the same ref (two plugins publishing one
PR), is left out of the graph and logged; the first stands.

Some kinds are **linking**: their entities stand for a unit of work, and link
what's about it. `ticket` (jira) and `notes.entry` are. `Graph.related(ref,
kind)` finds entities of `kind` one hop from `ref` in either direction, or
linked with it by a linking entity: a work entry naming APP-1 is related to
every PR whose branch, title or body mentions APP-1. A linking kind with a
pattern for its ids (`ticket`'s: a key in a project jira has seen a ticket
in, e.g. `APP-12`, so `UTF-8` isn't one) is found in other entities'
`mentions`, so they point at it without their plugin knowing the kind; see
[graph.md](graph.md#related-entities).

## Families

Core models what more than one tool may represent, in `move_forward.api.kinds`: the
`pull_request` family (`PullRequest`, shown as "PR"), `ticket` (`Ticket`) and `person`
(`Person`). A plugin's kind named for a family must extend its model, adding its own fields
(github's `number`, jira's `status`); the registry refuses one that doesn't. Refs to a
family's entities name the family, whoever publishes them, so `Ref("pull_request", url)`
finds a PR without knowing its plugin; `graph.of_kind("github.pull_request")` reads one
plugin's. Every family's model has
`handle`, how its tool shows an entity (`#7`, `APP-12`, a login): text names a family's
entities by handle (see [graph.md](graph.md)), and each entity says its `family`.

A family's ids are unique whoever publishes them, so two tools' `OPS-12`, or two people
named `alice`, are never taken for one; core leaves out (and logs) an entity whose id
breaks its family's rule:

| Family | id | `handle` |
| --- | --- | --- |
| `pull_request` | its URL | `#7`, `!12` |
| `ticket` | its URL | its key, `APP-12` |
| `person` | `<host>/<login>`, e.g. `github.com/alice`; mail's are `mail/<address>` | the login, or the address |

What the dashboard kept by a handle before (a priority on `APP-12`, a todo linked to it)
gets the entity's id at each refresh, once the graph can say it (`Graph.identify`); until
then it's kept as it is.

## Kinds

Every entity also says whether its work is over, in `lifecycle`: `done` for a merged or
closed `pull_request` and a `ticket` in a done status category (also when fetched), `open`
otherwise. `notes.entry` and `notes.todo` are published only while open, and are done once
gone.

| Kind | Published by | id | state | refs |
| --- | --- | --- | --- | --- |
| `pull_request` | github | the PR's URL | `open`, `merged` or `closed` | the `ticket`s its branch, title and body mention by key |
| `person` | github: me, and everyone it knows an email for; mail: whoever wrote a thread's latest message, when the server verified them (DMARC) | `<host>/<login>`; mail's `mail/<address>` | | |
| `mail.thread` | mail, while it has unread or flagged mail (gone means done) | the Message-ID it started with | `unread` or `flagged` | the `person` who wrote its latest message, if verified; it mentions its subject, then too |
| `git.repo` | git | GitHub `owner/name` | | |
| `git.branch` | git | `<repo dir>\t<branch>` | | its `git.repo` |
| `git_stack.stack_branch` | git_stack | its `git.branch`'s id | | its `git.branch`, which git may have deleted |
| `ticket` (linking) | jira | the issue's URL, `<site>/browse/<key>` | the issue's status | |
| `notes.entry` (linking) | notes | the entry's title | | the `pull_request`s and `ticket`s linked to it and its open todos (each open todo is a `notes.todo` that points back at its entry), and the `ticket`s its title mentions by key |
| `notes.todo` | notes | the todo's id (a number) | `open` (a ticked-off todo is gone) | its `notes.entry` |
| `notes.iced` (hiding) | notes | the act of icing's id | | what it puts on ice (a stack's `git.branch`es, or a `pull_request`, `ticket` or `notes.entry`), and what else that covers: the branches' `pull_request`s and `git_stack.stack_branch`es, an entry's refs |

`url_for(kind, id)` gives a URL for an entity that isn't in the graph: github
answers for `pull_request` (the id is the URL), jira for `ticket` (its id, or from its key
and `site`). `handle(kind, id)` gives its handle: jira's makes a ticket's key from its
URL.

## Fetched on demand

`Graph.fetch(kind, ids)` asks the plugin that owns a kind for entities by id, now, even ones
long gone from its snapshot; it's slow, so it's for rare work like the daily prune. A
fetched entity must say its `lifecycle`, and its attributes fit its kind's
`KindSpec.fetched_attrs`, or its family's fetched model (`api.kinds.PullRequestFetched`,
`TicketFetched`), whoever publishes it; core leaves out, and logs, one that doesn't. What a
fetched entity carries:

| Kind | Fetched by | state | Attributes |
| --- | --- | --- | --- |
| `pull_request` | github, with `gh pr view` | `open`, `merged` or `closed` | `closed_at`: ISO datetime or null, when it was merged or closed |
| `ticket` | jira, open or resolved; it remembers their projects | the ticket's status | `resolved_at`: ISO datetime, or null while open |

## Attributes

### `pull_request`

The pull request family, modeled in core (`api.kinds.PullRequest`); github adds `number`,
`base` and `author`.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `handle` | string | how its tool shows it, `#7`: for display, not identity |
| `repo` | string | `owner/name` |
| `number` | int | (github's) |
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
| `review_blockers` | list of strings | why reviewers shouldn't be nudged yet, including failing policy checks (other than those only waiting for a review), changes requested (but while waiting on a re-review) and missing requested members of an individual-policy team; empty when the PR is in order. `already approved` means it has the approval it needs, so there's nobody to chase |
| `last_review_activity` | ISO datetime or null | the latest review request, review or human comment |
| `review_stale` | bool | no review activity for `[github] stale_review_hours` |
| `stale_review_hours` | integer | `[github] stale_review_hours`, for what says how long a review request must be quiet |
| `failing_policy_checks` | list of strings | the failing checks named in `[github] policy_checks`, other than those only waiting for a review on a PR not yet approved |
| `progress` | string | where it is: `draft`, `ready to merge`, `CI failing` or `in review` |

### `person`

The person family, modeled in core (`api.kinds.Person`); github adds `me`. People with the same
`email` are one person, whichever tools publish them (`Graph.same_person`). An entity that
points at a person is about them, so core ranks and badges its items by their contact type.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `handle` | string | their name in the tool, a login; mail's is their address |
| `me` | bool | (github's) the person using the dashboard |
| `email` | string or null | their address; github's is their commit email, imported or looked up from commits |
| `contact_type` | string or null | what they are to me, when the tool knows, e.g. `customer`; one I set beats it |
| `importance` | 0, 1, 2 or null | how much they matter to me, when the tool knows (a CRM's VIP, a starred contact); the highest of it and their type's counts, but a type I set wins |

### `mail.thread`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `subject` | string | its latest message's |
| `sender_email` | string or null | who its latest message's `From` says wrote it, lower-case: unchecked (the thread points at a `person` only when the sender was verified) |
| `unread` | int | how many of its messages are unread |
| `flagged` | bool | flagged by whoever wrote its latest message (see docs/plugins/mail.md) |
| `bulk` | bool | its latest message is a list's, a newsletter's or automated |
| `to_me` | bool | its latest message has me among its `To`, not just copied |
| `latest` | ISO datetime | when the server received its latest message |

### `git.repo`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `dir` | string | the main checkout's path |
| `default_branch` | string | |
| `default_refs` | list of strings | the default branch on each remote (`origin/main`, `upstream/main`), or the local one when no remote has it, as refs `git log` takes in `dir` |
| `my_dirs` | list of strings | directories I committed to within `[git] commit_history_days` |

### `git.branch`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `ahead`, `behind` | int | commits ahead of and behind its upstream |
| `gone` | bool | its upstream was deleted |
| `upstream` | string or null | what it tracks, e.g. `origin/me/fix`; null when never pushed |
| `repo_dir` | string | the checkout it's in (git) |
| `name` | string | the branch's name (git) |
| `sha` | string or null | the commit it points at, as of the last refresh (git) |
| `checked_out_in` | string or null | the worktree it's checked out in |

### `git_stack.stack_branch`

A branch git-stack tracks, including one git no longer has.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `repo_dir`, `name` | string | its checkout and name, as `git.branch` has them |
| `stack_parent` | string | the branch it's built on |
| `stack_status` | string or null | git-stack's status, e.g. `up-to-date`, `needs-restack`, `merged` |
| `stack_group` | string or null | on a stack's bottom branch, the group its stack is in when the group has other stacks; git-stack's `restack`, `sync` and `push` act on the whole group |
| `stack` | list of strings | what git-stack acts on with it, by `git.branch` id: its stack, then the other stacks in its group, each bottom first, its own first of all |

### `ticket`

The ticket family, modeled in core (`api.kinds.Ticket`); jira adds `status`,
`status_category` and `type`.

| Attribute | Type | Meaning |
| --- | --- | --- |
| `handle` | string | how its tool shows it, its key `APP-12`: what text names it by |
| `summary` | string | |
| `status` | string | |
| `status_category` | string | Jira's `new`, `indeterminate` or `done` |
| `type` | string | the issue type |

### `notes.entry`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `merge_conditions` | object of lists of strings | what must happen before each of its PRs merges, by the PR's URL; a PR without conditions isn't in it |
| `open_todos` | list of strings | its open todos' text |

### `notes.todo`

| Attribute | Type | Meaning |
| --- | --- | --- |
| `entry` | string | its entry's title |
| `size` | string or null | `quick` or `big`; null while unsized |

### `notes.iced`

One act of icing, e.g. a stack. Core leaves items about whatever it hides off the page (see
[graph.md](graph.md#hidden-entities)).

| Attribute | Type | Meaning |
| --- | --- | --- |
| `iced_on` | string | ISO date |
| `note` | string or null | why it's on ice |
| `shas` | object | the commit each branch it put on ice pointed at then, by the branch's id |
