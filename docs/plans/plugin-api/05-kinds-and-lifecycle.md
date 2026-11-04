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
    handle: str           # how the tool shows it: "#7" (GitHub), "!12" (GitLab); display only
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

**Refs to a family's entities name the family.** A family's ids are unique across its
publishers (a PR's URL, a ticket's key), so `Ref("pull_request", url)` finds the PR
whoever publishes it. That's what notes needs when it finds a PR URL in a note, and slack
and jira when they read mentions: none of them know, or should know, the publisher. It
also keeps stored refs valid if another plugin starts publishing the same thing. A plugin's
own kinds are referred to by their full name (`Ref("git.branch", id)`). On an entity,
`ref` is its identity (the family for a family's kinds), `kind` its full name
(`github.pull_request`), `family` its family or None. Two publishers with one family id:
the first stands, logged, as two publishers of one ref are today.

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

- `api/kinds.py`: `Family(name, label, model)`; `PULL_REQUEST`, `TICKET`, `PERSON`. The
  family models take the fields some other plugin reads (git's, git_stack's, jira's,
  notes', slack's views decide which), except a tool's own identifiers: `number` becomes
  `handle` in the family and stays in `GitHubPullRequest`, and consumers show `handle`.
  The rest stay in `GitHubPullRequest`, `JiraTicket`, `GitHubPerson` (moved from
  `github/kinds.py`, `jira/kinds.py`).
- `check_plugins` (09; `check_names` until then) refuses a kind named for a family whose
  model doesn't subclass the family's.
- `Entity.family`; the graph indexes entities by ref, by full kind and by family;
  `graph.view`, `of_kind`, `related` and `get` take a family or a full kind. `url_for` on
  a family ref asks the plugin that published the id.
- Consumers import `api.kinds.PULL_REQUEST` and friends and drop their local `PR = "pr"`.
  The kind strings themselves are still today's in this PR (`pr` is the family's name until
  5c renames it), so nothing stored changes yet.
- `docs/entities.md`: a "Families" section (model, label, who publishes) and per-plugin
  kinds; `docs/graph.md` on families and reading them.

**Tests.** Two test plugins each publishing a `pull_request` kind both appear under the
family, each with its extras under its own kind; a plugin whose `pull_request` model
doesn't subclass `PullRequest` is refused with the reason; a family ref resolves to
whichever plugin published the id. **Size.** M/L.

## PR 5c — names: plugin-prefixed kinds, `pull_request` (core migration V11 + notes migration)

- Core publishes every kind as `<plugin>.<name>` and every family's refs by the family; a
  plugin makes refs to its own kinds with `ctx.ref(name, id)` (and `ctx.kind(name)` for
  the string); other plugins write the full string (`"git.branch"`,
  `"git_stack.stack_branch"`, `"notes.entry"`), as they write a family's constant.
- The `pr` family is renamed `pull_request`; `ticket` and `person` keep their names.
- Core migration, in `priorities.kind` and `act_now_feedback.subject_kind`: `pr` →
  `pull_request`, `branch` → `git.branch`, `repo` → `git.repo`, `stack_branch` →
  `git_stack.stack_branch`, `entry` → `notes.entry`, `todo` → `notes.todo`, `iced` →
  `notes.iced`. notes migration, in its link, todo link and ice item tables: `pr` →
  `pull_request`, `branch` → `git.branch`, `entry` → `notes.entry`.
- **User config**: `[act_now] snooze_days` keys are kinds (`pr = 7` → `pull_request = 7`,
  `entry = 2` → `"notes.entry" = 2`); a `Config` validator names the new key, like
  `MOVED_SETTINGS` (`config.py:33-42`). `mfd priority --kind` takes the new names (README
  row). Where a kind is shown (`mfd priority`'s messages, snooze notices), its label is.
- Anchors change (`page.anchor` is built from the kind); bookmarks to `#item-pr-…` and
  `#item-entry-…` break. `notifications.anchor` is transient.

**Tests.** Kinds are published prefixed and `ctx.ref` agrees; family refs aren't
prefixed; the migration over a V10 database with a row of each kind; notes' migration over
its tables; `snooze_days = { pr = 7 }` fails to load naming `pull_request`. **Risk.** Medium
(stored data). **Size.** M.

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
- Refs to a family's entities **by family** (links found in text and stored rows don't
  depend on the publisher) vs by full kind (the referrer would have to know the publisher).
- `Entity.lifecycle` as a two-value field (**recommended**) vs a richer status enum; `gone`
  is core's observation, not the publisher's, so it's a `KindSpec` flag.

## Dependencies

04 (hook style; `EntitiesRequest` is where `ctx.ref` is handy). 06 depends on this (it
rebuilds `act_now_feedback` and should see the rewritten kinds). 10 extends `KindSpec`
with `fetched_attrs` and makes `KindModel` strict, which the family models already use.
