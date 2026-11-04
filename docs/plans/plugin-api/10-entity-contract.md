# 10 — A tighter entity contract: strict models, one `pr` shape, opaque ids, core-held relations

## Problem 9: the entity contract is loose

- Kind models inherit `Model` with `extra="ignore"` (`domain.py`), so the check at
  `registry.py:160` passes a misspelt attribute: a publisher writing `requested_user`
  publishes nothing and nobody notices until a consumer's view fails.
- `fetch()` results aren't validated and are a second shape: github publishes `closed_at`
  only (`github/plugin.py:328-348`), jira `resolved_at` (`jira/plugin.py:389-407`), neither
  in `entities.md`'s attribute tables.
- Branch ids are `"<dir>\t<name>"`, built in two places (`git/panels.py:310`,
  `git_stack/panels.py:24`) and parsed in four (`git/panels.py:336`,
  `git/actions.py:90,116,142`); notes has its own `TARGET_SEPARATOR`
  (`notes/actions.py:21`).
- The PR↔branch join by `(repo, head)` is written four times, each differently:
  `git/panels.py:67-101` (upstream-name and same-sha fallbacks), `git_stack/panels.py:102-110`,
  `notes/covers.py:36-52`, `github/pushes.py:35-48`. A third-party consumer writes a fifth.
- notes re-implements git-stack's stack and group traversal (`notes/actions.py:69-106`).

**Before** for strict models, validated `fetch` shapes and id helpers (they change what a
publisher must do). **After** for relations and the git-stack membership attribute
(additive; consumers can keep their joins until then).

## PR 10a — strict kind models, validated `fetch`, id helpers (before the cut)

- `KindModel(Model)` with `extra="forbid"` as the base of every kind's attrs (core's shared
  ones from 05 already); `registry.build_graph` validates with it.
- `KindSpec.fetched_attrs`: the model `fetch()` results must fit (default: the kind's attrs
  with every field optional). github declares `PrFetched(closed_at)`, jira
  `TicketFetched(resolved_at)`; fetched entities must carry `lifecycle`. `Graph.fetch`
  validates and logs/drops misfits. `entities.md`'s "Fetched on demand" table is checked by
  `test_entities_doc.py` like the others.
- `api.kinds.branch_id(repo_dir, name)` / `branch_parts(id)`; git, git_stack and notes use
  them; the three separator constants go. Format unchanged (decision below).

**Tests.** A misspelt attribute is rejected (flip the assertion that today's
`_Declaring` test leaves implicit); a fetched entity without `lifecycle` is dropped;
`branch_parts(branch_id(d, n)) == (d, n)`. **Size.** M.

## PR 10b — `relate` hook and stack membership (after the cut)

- `Plugin.relate(RelateRequest(ctx, graph)) -> list[tuple[Ref, Ref]]`, run by core after
  every `entities`; results indexed like `refs`. github relates each `pr` to the `branch`
  it comes from using git's `repo` entities, folding in the upstream-name and same-sha
  fallbacks from `git/panels.py:81-101`. Then `git/panels.py`, `git_stack/panels.py:102-110`,
  `notes/covers.py:36-52` and `github/pushes.py` read `graph.related(branch, PR)`.
- git_stack publishes `stack: list[str]` (the stack's branch ids, group included) on each
  `stack_branch`; notes' `_stack_branches` becomes a lookup.
- `docs/graph.md` (a "Relations" section), `plugins.md`.

**Tests.** Table-driven: renamed branch, same-sha branch, two repos; the four consumers
agree; notes ices the whole group from the attribute. **Size.** L.

**Migration/compat.** None stored in either PR.

**Risk.** 10a low; 10b medium (today's four joins differ; git's is the richest, so take it
and test the others against it).

## Decisions

- Branch id format: **keep the tab** behind helpers; `"<dir>#<name>"` would migrate notes'
  `notes_ice_items.ref_id` and `iced.shas` keys for no consumer-visible gain.
- Relations as a publisher hook (**recommended**: core stays tool-agnostic) vs a core join
  rule on shared attrs (`pr.repo + pr.head` vs `branch`): the hook can use the fallbacks.

## Dependencies

05 (`KindSpec`, shared kind models). 10b after the cut; 12's harness should offer
`with_relations` once 10b exists.
