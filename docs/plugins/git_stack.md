# git_stack

Stacks of branches from [git-stack](https://github.com/benekastah/git-stack), in `[git]`'s
repos. Enabled by a `[git_stack]` section, which needs `[git]`.

## Config

`[git_stack]` has no settings. `mfd init` adds it when git-stack is installed; add it by
hand to an older config. Its buttons need a git-stack whose commands take `--json`.

## Publishes

`stack_branch` for each branch git-stack tracks, pointing at git's `branch` (which git may
have deleted), with its parent, status and, on a stack's bottom branch, its group; see
[entities.md](../entities.md).

## Panels

- **Stacks**: `git stack info` statuses other than up-to-date, and tracked branches with
  no PR, with a button per repo that runs `git stack clean -d local` for its merged
  branches. Its count is about right: it comes from the last offline `git stack info`,
  while clean fetches and asks GitHub. It isn't offered while git-stack tracks an iced
  stack in the repo, since clean covers every stack; untracking the iced stack (On ice's
  Untrack) brings it back. Clean refuses while tracked files in the repo's checkout have
  uncommitted changes.

On notes' On ice panel, git_stack adds Untrack to a stack while git-stack tracks its
branches.

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/git_stack/untrack` | `targets` (lines of `<repo dir>\t<branch>`) | `git stack untrack --json` for each repo's, saying which branches still stacked on them now start their own stacks. |
| `/actions/git_stack/stack_clean` | `repo` | `git stack clean -d local --json`, saying which branches it deleted, which it untracked but kept, and its warnings, or, when it fails, what it did first; refused while git-stack tracks an iced stack in the repo. |
