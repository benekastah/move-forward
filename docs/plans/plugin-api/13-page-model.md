# 13 — A page model without tool words

## Problem 14: the page model carries tool-specific fields

- `Link.pr_state` (`page.py:32`; set at `notes/panels.py:127,132`; rendered as a CSS class,
  `render.py:342`): a Linear issue link can't be struck through when done.
- `Item.draft` (`page.py:103`; sorted last `panels.py:169`; filtered out of Act now in
  `github/plugin.py:362-375`; reset in `act_now.py:515`): "draft" is a PR word for a
  generic "sorts after everything".
- Badge colour by word matching (`render.py:213,285-287`): a badge saying "unblocked" is red
  because it contains "blocked".
- `Item.pending` is core-internal JSON on the public model (`page.py:114-117`,
  `act_now.py:500-509`); a plugin could set it and confuse the pending script.

**After** the cut: no stored data, one consumer each, renames are one-line breaks; the
`pending` removal belongs with subplan 14, which replaces it.

## PR 13 — generic fields

**Changes.**
- `Link.pr_state` → `Link.done: bool`; notes sets it from the PR's `lifecycle` (05).
- `Item.draft` → `Item.deferred: bool` ("sorts after everything in its panel; Act now
  leaves it out unless the plugin nominates it explicitly"); core applies the Act now rule
  so github's override goes.
- `Item.badges: list[str | Badge]`, `Badge(text, tone: Literal["plain", "warn", "good"])`;
  render maps tone to class; built-ins set `warn` where today's words matched;
  `WARNING_BADGE_WORDS` deleted.
- `Item.pending` stays until 14b removes it (noted there).

**Migration/compat.** None. **Tests.** Render tests for tone and `done`; a `deferred` item
sorts last and isn't nominated. **Risk.** Low. **Size.** M.

## Decisions

- `Badge` as a model alongside plain strings (**recommended**: no churn for the common
  case) vs all badges as models.

## Dependencies

05 (`lifecycle` for `Link.done`). 14b removes `Item.pending`.
