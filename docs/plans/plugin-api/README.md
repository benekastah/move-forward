# Plugin API redesign

Goal: let other people write plugins as separate packages, with the breaking changes done
first, as stacked PRs (git-stack), each green and each leaving `mfd serve --reload`
working. One subplan per problem (or per group of problems the same code and migration
resolve). Line numbers are as of `main` at `4cd7d63`.

## Problems, and the subplan that fixes each

| # | Problem (plain words) | Subplan | Before inviting others? |
| --- | --- | --- | --- |
| 1 | Entity kinds are global bare strings owned by whoever declares them first | [05](05-kinds-and-lifecycle.md) | yes |
| 2 | Core knows tool states (merged, closed, `status_category`) | [05](05-kinds-and-lifecycle.md) | yes |
| 3 | Hook signatures are positional; adding an input breaks every subclass | [04](04-hook-request-objects.md) | yes |
| 4 | No public API module, no API version | [02](02-public-api.md) | yes |
| 5 | Plugins are hard-coded in core; reserved names aren't | [09](09-discovery-and-config.md) | yes |
| 6 | One failing hook 500s every page and stops every refresh; `collect` has no timeout | [01](01-isolate-hook-errors.md) | yes |
| 7 | Plugins hand-build qualified names; page paths are global; sign-in pages bypass auth | [07](07-qualified-names-and-pages.md) | yes |
| 8 | Act now feedback is keyed by display text | [06](06-feedback-identity.md) | yes |
| 9 | Entity contract is loose: unvalidated attrs, two `pr` shapes, composite ids, hand-rolled joins | [10](10-entity-contract.md) | strict models and ids yes; relations after |
| 10 | Hooks lack inputs they need (graph, previous graph, surface, refresh scope, reason, identity) | [04](04-hook-request-objects.md) | yes |
| 11 | `Context` is thin (no name, log, data dir; kv has get/set only) | [03](03-context.md) | yes |
| 12 | Conventions are implicit, read with `getattr`; magic panel numbers; cross-plugin `rank` | [08](08-explicit-conventions.md) | hooks yes; bands and rank after |
| 13 | Migrations are index-counted SQL strings, no prefix check | [11](11-named-migrations.md) | format yes; core-table coupling deferred |
| 14 | Page model carries tool words (`pr_state`, `draft`, badge word-matching, `pending`) | [13](13-page-model.md) | after |
| 15 | Opened items are hidden for 10 min and refetch the whole plugin; no per-item refresh | [14](14-refresh-subjects.md) | after (new feature) |
| 16 | Nothing an out-of-tree plugin's tests can import | [12](12-testing-harness.md) | yes |

Deferred, not in any subplan: built-ins' first migrations reading core's V1 tables and
`snapshots.payload` (problem 13's second half: keep, and freeze `V1_SCHEMA` for them);
a `plugins = []` allow-list in the config; ticket↔PR relations beyond `mentions`.

## Order and dependencies

Do them in file order, with 02's second PR (the `api_version` freeze) after 12:

```
01 isolate ─┐
02 api (PR A) ─┐
03 context ──┐ │
04 requests ─┴─┴─┐          needs 02 (new types exported from api), 03 (ctx.log)
05 kinds ────────┤          needs 04 (KindSpec sits beside the new hook style; migrations)
06 feedback id ──┤          needs 05 (kind names in act_now_feedback are rewritten there first)
07 names/pages ──┤          needs 04 (ctx.action, Response on PageRequest)
08 conventions ──┤          needs 04
09 discovery ────┤          needs 07 (RESERVED paths), 08 (teams_field, token_settings)
10 entity contract (PR A) ─┤ needs 05
11 named migrations ───────┤ needs 06 (its migration drops the CHECK this one relies on)
12 testing harness ────────┤ needs 09 (constructs plugins without the registry list)
02 api (PR B: api_version, docs) ── after everything above: the freeze
── cut line ──
10 entity contract (PR B: relations)
13 page model
14 refresh subjects          needs 06 (no table rebuild), 13 (Item.pending removal)
08 conventions (PR B: bands, rank)
```

## Principles for the stack

- One logical change per PR; a core signature change and the built-ins' move land in the
  same PR (AGENTS.md's "core first, plugin second" is for new features; a signature
  change split across PRs fails `mfd check` and stalls `--reload`).
- Within a PR, commit in steps that each import cleanly: add the new name, switch callers,
  remove the old.
- Stored-data changes early, each with a core or plugin migration; a backup is taken
  before the first migration (`db.py:285-317`).
- Every PR updates `docs/writing-a-plugin.md`, `AGENTS.md` and the plugin pages it makes
  untrue, in the same PR.
- `python3 dev.py check` per commit; CI runs it on three OSes.
- Sizes: S < 150 lines changed, M < 500, L < 1200; anything larger is split.

## Decisions needed (recommendations in bold)

1. **Kinds** ([05](05-kinds-and-lifecycle.md)): **families modeled in core (`pull_request`,
   `ticket`, `person`; `email` with its first publisher), which a plugin's kind extends;
   every kind named by its plugin (`github.pull_request`, `git.branch`); refs to a
   family's entities by the family; migrated now**. Not namespacing with no core models
   (what a PR has would be a convention nobody owns) and not "first declarer" (names
   collide).
2. **Hook style** ([04](04-hook-request-objects.md)): **one frozen request dataclass per
   hook**; `ctx` stays the plugin's environment.
3. **URL namespace** ([07](07-qualified-names-and-pages.md)): **`/<plugin>/…`** (keeps
   `/slack/callback` and the Slack manifest) over `/p/<plugin>/…`.
4. **Feedback backfill** ([06](06-feedback-identity.md)): **slugify existing verbs** (github's
   `Review` survives) over dropping all feedback.
5. **Plugin order and identity** ([09](09-discovery-and-config.md)): **alphabetical by name;
   footer lists every plugin's identity** rather than first-wins.
6. **Discovery** ([09](09-discovery-and-config.md)): **built-ins listed in core, entry
   points for other packages**; a config section enables, an entry point installs.
7. **Opened cap** ([14](14-refresh-subjects.md)): **24 h**, cleared earlier when the data
   changes.
8. **Refresh after an action** ([04](04-hook-request-objects.md)): **the acting plugin, and
   the publishers of what it touched** (`ActionResult.touched: list[Ref]`), rather than
   naming plugins or refreshing all.
9. **Family names** ([05](05-kinds-and-lifecycle.md)): **`pull_request`** (shown as "PR")
   and `ticket`; no tool's numbering in a family (a PR has a `handle` to show, its URL to
   identify it). Not `code_review`/`work_item`: the names fit a GitLab MR or a Linear
   issue.
10. **Named migrations before the freeze?** ([11](11-named-migrations.md)): **yes**; the
    list format is the one thing that can't change after others ship lists.
11. **Branch id format** ([10](10-entity-contract.md)): **keep `"<dir>\t<name>"` behind
    helpers**; changing it migrates notes' ice tables for nothing.
12. **Collect timeout default** ([01](01-isolate-hook-errors.md)): **300 s**, per-plugin
    override, per piece: a plugin with more work returns `Collected(more=True)` (04).
13. **Family identity** ([05](05-kinds-and-lifecycle.md)): **each family's own rule**: URLs
    for PRs and tickets, `<host>/<login>` for people; text names them by `handle`.
14. **Previous graph** ([04](04-hook-request-objects.md)): **the last one built, or after a
    start the one built from stored snapshots**; nothing new stored.

## Minimum before inviting others

01, 02 (PR A), 03, 04, 05, 06, 07, 08 (PR A), 09, 10 (PR A), 11, 12, then 02 (PR B). After
that line the API is additive: 10 (PR B), 13, 14, 08 (PR B) can land in any order.
