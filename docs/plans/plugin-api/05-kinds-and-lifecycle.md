# 05 — Kinds: families modeled in core, every kind named by its plugin, lifecycle

Groups problems 1 and 2: both are "who owns the meaning of a kind", and the lifecycle flag
that fixes 2 lives in the same `KindSpec` and the same migration that fixes 1.

## Problem 1: kinds are global bare strings owned by whoever declares them first

`Plugin.kinds` (`plugin.py:108`); ownership by iteration order (`registry.py:140-147`).
Every consumer hard-codes `"pr"` (`jira/plugin.py:27`, `git/panels.py:17`,
`git_stack/panels.py:18`, `notes/panels.py:18`, `notes/covers.py:10`, `slack/pings.py:47`).
A GitLab plugin can't publish PRs: as `pr` it loses to github, as `mr` it's invisible to
git, jira, notes and slack. Kind strings are persisted: `priorities.kind` (`db.py:138-146`),
`act_now_feedback.subject_kind` (`db.py:149-163`), notes' link, todo link and ice tables
(`notes/repository.py:43,57,88`), `[act_now] snooze_days` keys (`act_now.py:101`).
**Before**: the day a third party ships a kind, its name is in users' databases.

## Problem 2: core knows tool states

`priority._DONE_STATES = {"merged", "closed", "done"}` (`priority.py:13`); `_finished`
special-cases `ticket.status_category` (`:50-55`); `forget_finished` names `pr`, `ticket`,
`entry`, `todo` (`:72,76`); `RESOLVED_KINDS` (`:29`) decides what `mfd priority` can name.
A Linear plugin's done issues keep their priority forever and `mfd priority ENG-12 1` says
"no entity the dashboard knows". **Before** (it's the other half of 1).

## Design

**Families are modeled in core.** What more than one tool may represent has a model in
`api.kinds`, owned by core: `PullRequest` (family `pull_request`), `Ticket` (`ticket`),
`Person` (`person`). `Email` (`email`) joins when the first mail plugin needs it: its
fields come from a real publisher, not a guess. Each family has a display label (`PR`,
`ticket`, `person`), so the page and the CLI say "PR" while the code says `pull_request`.

**Every kind is named by its plugin.** A plugin declares bare names in `kinds`; core
publishes them as `<plugin>.<name>`: `github.pull_request`, `github.person`,
`jira.ticket`, `git.branch`, `git.repo`, `git_stack.stack_branch`, `notes.entry`,
`notes.todo`, `notes.iced`. No two plugins' kinds can collide, and no plugin owns a name
by being first.

**A plugin's kind in a family extends the family's model.** A kind whose bare name is a
family (`pull_request`) must declare a subclass of the family's model, adding its own
fields; core refuses the plugin otherwise, with the reason in `mfd check`. So every
`*.pull_request` has every field of `PullRequest`, and a plugin can't use a family's name
for something else.

```python
# api.kinds (core)
class PullRequest(KindModel):
    repo: str
    handle: str  # what the tool shows: "#7" (GitHub), "!12" (GitLab); display only
    head: str
    head_oid: str | None = None
    base: str
    author: str | None = None
    draft: bool = False
    mine: bool
    ...


# github
class GitHubPullRequest(PullRequest):
    number: int
    review_blockers: list[str] = []
    review_stale: bool = False
    ...


kinds = {"pull_request": KindSpec(GitHubPullRequest), "person": KindSpec(GitHubPerson)}
```

**A family model holds nothing about how a tool numbers things.** A PR's identity is its
ref's id (its URL); a tool may number PRs, as GitHub does, or give them a UUID. The family
has `handle`, the short text the tool shows people (`#7`, `!12`), for display only, and a
plugin keeps its own identifiers as extras (`GitHubPullRequest.number`). Consumers that
show "PR #7" today (`git/panels.py`) show `handle`.

**Each family says what identifies its entities, unique whoever publishes them.** Today's
ids aren't all unique across tools: a ticket's key (`jira/plugin.py:35`) can be `OPS-12` in
Jira and in Linear, or on two Jira sites, and a person's login (`github/plugin.py:324`) can
be `alice` on GitHub and someone else on GitLab. So:

| Family | Id | `handle` (shown, and matched in text) |
| --- | --- | --- |
| `pull_request` | its URL | `#7`, `!12` |
| `ticket` | its URL | its key, `APP-12` |
| `person` | `<host>/<login>`, `github.com/alice` | the login |

`Family.id_rule` checks each id as entities are published (an absolute URL, or
`<host>/<login>`), and an entity whose id doesn't fit is dropped and logged.

**Text names a family's entities by handle.** `linking` patterns (`jira`'s ticket keys)
match handles, and the graph maps a handle found in text to the entities with that handle
(`graph.by_handle(family, handle)`): all of them when two trackers share a key, since a
mention can't say which.

**Refs to a family's entities name the family.** With ids unique across publishers,
`Ref("pull_request", url)` finds the PR whoever publishes it. That's what notes needs when it finds a PR URL in a note, and slack
and jira when they read mentions: none of them know, or should know, the publisher. It
also keeps stored refs valid if another plugin starts publishing the same thing. A plugin's
own kinds are referred to by their full name (`Ref("git.branch", id)`). On an entity,
`ref` is its identity (the family for a family's kinds), `kind` its full name
(`github.pull_request`), `family` its family or None. Two publishers with one family id
publish the same thing (two plugins reading one GitHub): the first stands, logged.

**Reading.** `graph.view(PULL_REQUEST)` gives every publisher's PRs as `PullRequest`
(the plugin's extra fields left out); a consumer that needs github's extras asks for
`graph.view("github.pull_request", GitHubPullRequest)` by name, so that coupling shows.
Consumers' own small views keep working, typed against the family's fields.

**What stays a plugin's own.** `branch` and `repo` are git's (`git.branch`, `git.repo`):
nothing else could publish them today. If a second one appears (jj), they become families
then; adding a family is additive for publishers.

## PR 5a — `KindSpec` and `Entity.lifecycle` (no migration)

- `KindSpec(attrs: type[KindModel], linking: str | None | Literal[False] = False,
  hides=False, fetchable=False, gone_means_done=False, resolvable=False)`;
  `Plugin.kinds: dict[str, KindSpec]` replaces `kinds` + `linking` + `hides`
  (`plugin.py:108-119`). `linking_patterns` stays as the dynamic narrowing.
- `Entity.lifecycle: Literal["open", "done"] = "open"` beside the tool's `state`. github
  sets it by list (`github/plugin.py:301-309`); jira by `status_category == "done"`; notes'
  done todos aren't published, so `todo` and `entry` get `gone_means_done=True`; `branch`
  stays `False` (git's refresh may have failed, `priority.py:61-62`).
- `priority.py`: delete `_DONE_STATES`, `_finished`, `RESOLVED_KINDS`; `forget_finished`
  uses `lifecycle`, `fetchable` (what to look up) and `gone_means_done`; `resolve` tries
  the kinds marked `resolvable`.
- `tests/test_entities_doc.py` reads `KindSpec`; `entities.md` gains a lifecycle column.

**Tests.** `forget_finished` driven by `lifecycle` with no kind names in the test; a
`gone_means_done` kind clears when missing, a plain one doesn't. **Size.** M.

## PR 5b — families in core (no migration)

- `api/kinds.py`: `Family(name, label, model, id_rule)`; `PULL_REQUEST`, `TICKET`,
  `PERSON`; `handle` on every family model. The
  family models take the fields some other plugin reads (git's, git_stack's, jira's,
  notes', slack's views decide which), except a tool's own identifiers: `number` becomes
  `handle` in the family and stays in `GitHubPullRequest`, and consumers show `handle`.
  The rest stay in `GitHubPullRequest`, `JiraTicket`, `GitHubPerson` (moved from
  `github/kinds.py`, `jira/kinds.py`).
- `check_plugins` (09; `check_names` until then) refuses a kind named for a family whose
  model doesn't subclass the family's.
- `Entity.family`; the graph indexes entities by ref, by full kind, by family and by
  handle; `graph.view`, `of_kind`, `related` and `get` take a family or a full kind;
  `graph.by_handle(family, handle)`. `url_for` on a family ref asks the plugin that
  published the id. `linking` matches handles (`graph.py:41-67` matches ids today).
- Consumers import `api.kinds.PULL_REQUEST` and friends and drop their local `PR = "pr"`.
  The kind strings themselves are still today's in this PR (`pr` is the family's name until
  5c renames it), so nothing stored changes yet.
- `docs/entities.md`: a "Families" section (model, label, who publishes) and per-plugin
  kinds; `docs/graph.md` on families and reading them.

**Tests.** Two test plugins each publishing a `pull_request` kind both appear under the
family, each with its extras under its own kind; a plugin whose `pull_request` model
doesn't subclass `PullRequest` is refused with the reason; a family ref resolves to
whichever plugin published the id; an id breaking its family's rule is dropped; text naming
a key two trackers share links both tickets. **Size.** M/L.

## PR 5c — plugin-prefixed kinds, `pull_request` (core migration V11, notes migration)

- A plugin's own kinds are named for it: it declares and writes the full name
  (`kinds = {BRANCH: ...}` with `BRANCH = "git.branch"`), as other plugins write it. The
  registry refuses a kind named neither for the plugin nor for a family. A family's kinds
  are declared and referred to by the family (`pull_request`); core sets `Entity.kind` to
  the publisher's full kind (`github.pull_request`) and indexes it, so `of_kind` and `view`
  take a family or a full kind.
- The `pr` family is renamed `pull_request`; `ticket` and `person` keep their names.
- Core migration, in `priorities.kind` and `act_now_feedback.subject_kind`: `pr` →
  `pull_request`, `branch` → `git.branch`, `repo` → `git.repo`, `stack_branch` →
  `git_stack.stack_branch`, `entry` → `notes.entry`, `todo` → `notes.todo`, `iced` →
  `notes.iced`. notes migration, in its link, todo link, ice item and cover tables: `pr` →
  `pull_request`, `branch` → `git.branch`, `entry` → `notes.entry`.
- **User config**: `[act_now] snooze_days` keys are kinds (`pr = 7` → `pull_request = 7`,
  `entry = 2` → `"notes.entry" = 2`); a validator names the new key. Where a family's kind
  is shown (`mfd priority`'s messages), its label is.
- **The skill** reads `CHANGELOG.md`, new here, and updates itself: the kinds it passes to
  `mfd notes ice --item <kind> <id>` and `mfd priority --kind`, and reads in `mfd notes`
  and `mfd snoozes` JSON. The CLI takes only the new names: there's one user, so no
  compatibility shim.
- Anchors change (`page.anchor` is built from the kind); bookmarks to `#item-pr-…` and
  `#item-entry-…` break. `notifications.anchor` is transient.

**Tests.** A kind not named for its plugin is refused; two plugins publish one family, each
under its own full kind, and a shared id is the first one's; the core migration over a V10
database with a row of each kind; notes' migration over its tables; `snooze_days = { pr =
7 }` fails to load naming `pull_request`. **Risk.** Medium (stored data). **Size.** L.

## PR 5d — family ids (no SQL migration; kept rows re-keyed at refresh)

- `Family.id_rule` checks ids as entities are published (an absolute URL, or
  `<host>/<login>`); an entity breaking it is dropped and logged.
- jira publishes tickets by URL (`<site>/browse/<key>`, the key as `handle`), github people
  as `<host>/<login>` (the login as `handle`). github's own `github_people` table stays
  keyed by login: it's github's.
- Showing and finding by handle: `graph.handle(kind, id)` (the entity's `handle`, else the
  new `handle_for` hook: jira makes a key from a URL), `graph.identify(kind, id)` (the one
  entity with that handle, else the id the plugin's `url_for` makes: a URL from a key), and
  `view_by_handle`. A mention of a key no entity in the graph has becomes a ref by the URL
  jira makes for it, so a note's link and a PR's mention of one ticket stay one ref. The
  CLI still takes keys (`mfd priority APP-1`, `mfd notes ice --item ticket APP-1`).
- Kept rows by a stored key can't be rewritten by SQL: a migration doesn't know Jira's site
  or GitHub's host. At every refresh, core (`priorities`, `act_now_feedback`) and notes (its
  links, todo links and ice) run the ids they keep of a family's kinds through
  `graph.identify`; where the new id is there already, the old row goes. A row nothing can
  identify (no jira) is kept as it is, and converts once jira can say; so no kv flag.
  slack keeps logins (its contacts and pings are GitHub's people by login) and finds the
  `person` by handle, so it has nothing to re-key.
- `CHANGELOG.md` says what changed for the skill.

**Tests.** Ids fit their family's rule and an entity breaking it is left out; `identify` and
`handle` from the graph, else the plugin; an unknown key is named by its URL; core's and
notes' kept keys get their tickets' URLs, a newer row wins a clash, and without jira they're
kept. **Risk.** Medium (stored data). **Size.** L.

## Decisions

- Kinds model:
  - (a) Namespace every kind and read families by suffix (`*.pr`), each consumer checking
    publishers' fields against its own view, with no model in core. Core stays
    tool-agnostic, but what a PR has is a convention nobody owns: a publisher missing a
    field drops out of a consumer's panel.
  - (b) Shared kind names owned by core, publishers listed per kind, private kinds bare
    and first-declarer: private names collide.
  - (c) **Chosen**: families modeled in core, which plugins extend; every kind named by
    its plugin; refs to a family's entities by the family.
- Name of the PR family: **`pull_request`**, shown as "PR"; `pr` was terse enough to read
  as something else, and the rename rides on the migration that 5c makes anyway.
  `code_review`/`work_item` for `pull_request`/`ticket`: no, the names fit a GitLab MR or a
  Linear issue.
- Which families: **`pull_request`, `ticket`, `person` now; `email` with its first
  publisher**; `branch` and `repo` stay git's until a second publisher exists.
- Identity: **each family's own rule** (URLs for PRs and tickets, `<host>/<login>` for
  people) vs `person` as a plugin's kind with only email shared (tickets would still need
  URLs).
- Refs to a family's entities **by family** (links found in text and stored rows don't
  depend on the publisher) vs by full kind (the referrer would have to know the publisher).
- `Entity.lifecycle` as a two-value field (**recommended**) vs a richer status enum; `gone`
  is core's observation, not the publisher's, so it's a `KindSpec` flag.

## Dependencies

04 (hook style; `EntitiesRequest` is where `ctx.ref` is handy). 06 depends on this (it
rebuilds `act_now_feedback` and should see the rewritten kinds). 10 extends `KindSpec`
with `fetched_attrs` and makes `KindModel` strict, which the family models already use.
