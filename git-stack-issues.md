# git-stack issues

Where the git_stack plugin (and notes' use of its data) disagrees with what git-stack does,
and what git-stack could add to help. Most severe first.

## Icing one stack of a group leaves the rest of the group able to change it

`notes/actions.py:_stack_branches` walks `stack_parent` to find a branch's stack, the way
`git stack show-stack` would without groups. But git-stack's "current stack" includes every
stack in the branch's group, so `git stack sync`, `restack` or `push` from another stack in
the group restacks and force-pushes the iced stack too. `plan.md` says the group's other
stacks "aren't iced with it", which undersells this.

The plugin already stores each branch's group (`git_stack_branches.stack_group`, from
`git stack info --json`), but `StackBranchAttrs` doesn't publish it. Publishing it as
`stack_group`, and having `_stack_branches` add the other stacks in the group, would ice
what git-stack treats as one stack.

## An iced stack blocks Clean even after it's untracked

`iced_stacks` counts every iced branch git still has, and `stack_clean` (and the panel)
refuse to clean a repo with any. `git stack clean` only touches branches git-stack tracks,
so once the iced stack has been untracked (the On ice panel's Untrack button), clean can't
affect it. Only iced branches git-stack still tracks (a `stack_branch` exists for them)
should block it.

## "clean per stack instead" names a command that doesn't exist

`stack_clean`'s refusal says "clean per stack instead", and the panel says `git stack clean`
"is left to you". `git stack clean` takes no stack arguments: it always covers every stack.
The per-stack alternatives are `git stack sync <branch>` or `sync -g <group>`, which also
restack and push, or `git stack untrack` of the merged branches by hand. See the proposed
`clean` selectors below.

## Clean's "N deleted" count is off by one

`stack_clean` counts stdout lines containing "deleted". `git stack clean` always prints
"fetching origin, and pruning its deleted branches" to stdout, so a clean that deletes
nothing reports "1 deleted". It ends by printing every stack as a tree, so a branch whose
name has "deleted" in it is counted too. The lines for deleted branches read
"<branch> was merged into <parent>; deleted it (was <sha>)"; matching "; deleted it (was "
fixes it until git-stack has a structured output (see `clean --json` below).

## Clean's button can promise more, or less, than it does

- The "Clean N merged" count comes from `git stack info --offline`, which checks for merges
  locally against the roots as of the last fetch. `git stack clean` fetches, fast-forwards
  the roots and asks GitHub, so it can untrack a different set.
- The confirm text says it fast-forwards main; it fast-forwards every stack's root.
- `git stack clean` refuses to run with uncommitted changes (staged or not; untracked files
  are fine), which the confirm text doesn't mention, so the button often just fails. See
  "clean's clean-worktree check" below.

## Proposed git-stack additions

- **`git stack clean <branch>...` / `-g <group>`**: clean only the named stacks, as `sync`
  takes them. The dashboard could then offer Clean in a repo with iced stacks, cleaning the
  others, and "clean per stack" becomes true.
- **`git stack clean --json`** (and perhaps `sync --json`): what was untracked, deleted and
  warned about, so callers don't scrape human-readable lines.

`show-stack --json` isn't needed: `git stack info <branch> --json` already gives a branch's
whole stack, groups included.

## clean's clean-worktree check

`git stack clean` calls `ensure_clean_worktree()` up front. It only touches the working tree
in two places:

1. Fast-forwarding a root that's checked out (`git merge --ff-only`).
2. With `-d local` or `-d both`, deleting the checked-out branch, which first switches to a
   child or the parent.

Both can fail with uncommitted changes that conflict, and the second happens partway
through `remove_merged`, after the children have been moved onto the new parent, which would
leave the metadata half-updated. The up-front check avoids that by refusing before anything
changes.

But it's broader than needed: when the checked-out branch is neither a stack's root nor
(with `-d local`/`both`) a tracked branch, clean never touches the working tree, and the
check refuses for nothing. That's the usual case from the dashboard: on a feature branch
with work in progress, cleaning other merged branches. Narrowing the check to those two
cases (still before anything changes) would keep the protection and let the button work
far more often.
