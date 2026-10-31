# Typed entity schemas

A plan for giving entity kinds and their attributes a schema in code, instead of only
the prose in `docs/entities.md`. Not started; see `todo.md`.

## Where things stand

- Kinds are open: `Ref.kind` is any string and `Entity.attrs` is a
  `dict[str, JsonValue]` (`graph.py`). Core validates neither.
- Core knows two kinds by name: `TICKET` drives `Graph.related` (sharing a ticket makes
  entities related), and `refresh.py` finds the `person` with `me`.
- The only schema is `docs/entities.md`. Keeping code and doc in step is left to review.
- Consumers read attributes defensively: `str(pr.attrs.get("head"))`,
  `bool(pr.attrs.get("review_stale"))`, slack's `_strings(...)`. A publisher renaming or
  dropping an attribute doesn't fail anything: consumers quietly get `"None"`, `False` or
  `[]`. The contract is deliberate on paper only.

## Why not a schema per plugin

Each plugin declaring the schema of what it publishes doesn't work on its own:

1. **Some kinds have several publishers.** github and slack both publish `person`;
   git-stack adds `stack_parent` to git's `branch`; a second code host would publish
   `pr`. Per-plugin schemas leave no owner for a shared kind, and need rules for
   extending another plugin's kind.
2. **Consumers can't import a publisher's types**, since plugins never import each other.
   A publisher-only schema protects what's published, but consumers still read untyped
   dicts.

## The plan

- **Shared kinds are typed models in core**: `pr`, `person`, `repo`, `branch`, `ticket`.
  They're tool-neutral concepts (a GitLab plugin would publish `pr` too), so this fits
  "core knows no tool" as long as the attributes stay neutral. Some aren't today:
  `ticket`'s `status_category` uses Jira's vocabulary, and `person`'s `slack_id` belongs
  to Slack. Every field is optional, which is what lets several publishers merge into one
  entity.
- **A plugin's private kinds** (`entry`, `iced_branch`, git-stack's) are models the
  plugin declares and registers; core validates published entities against them when it
  builds the graph. Their kinds are namespaced (`notes.entry`), like the `<plugin>.<panel>`
  panel keys of step 6.
- **Extending another plugin's kind** (`stack_parent` on `branch`) is either a field on
  the core model, or a namespaced attribute (`git_stack.parent`) declared by the plugin
  adding it.
- **Consumers read typed attributes**, e.g. `graph.of_kind(PR)` giving `PrAttrs`, or a
  validating accessor, so a rename fails basedpyright and the tests rather than the page.
- **`docs/entities.md` is generated from the models**, or a test checks that they match,
  so the doc can't drift.

## Cost

Changing a shared kind becomes a core change, and so its own stacked PR. That matches
how `AGENTS.md` already treats the contract.
