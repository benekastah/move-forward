# git_stack

Stacks of branches from [git-stack](https://github.com/benekastah/git-stack), in `[git]`'s
repos. Enabled by a `[git_stack]` section, which needs `[git]`.

## Config

`[git_stack]` turns it on. `mfd init` adds it when git-stack is installed; add it by hand
to an older config. `refresh_seconds` (300) is how often it refreshes regardless. It
watches git: after each refresh of git it compares git's branches in its repos, and each
repo's `.git/config` (where git-stack records each branch's parent, base and group), with
what it saw last, and runs `git stack info` again only when they moved. Its buttons need a git-stack whose commands take `--json`.

## Publishes

`stack_branch` for each branch git-stack tracks, pointing at git's `branch` (which git may
have deleted), with its parent, status and, on a stack's bottom branch, its group; see
[entities.md](../entities.md).

## Panels

- **Stacks**: `git stack info` statuses other than up-to-date, and tracked branches with
  no PR, with a button per repo that runs `git stack clean -d local` for its merged
  branches. Its count is about right: it comes from the last offline `git stack info`,
  while clean fetches and asks GitHub. It covers every stack, iced ones too: it never
  restacks or pushes, so an iced stack is left as it is unless a branch of it merged, and
  reopening the stack restores a deleted branch at its iced commit. With uncommitted changes in the repo's checkout, clean refuses
  only if the branch checked out there is a stack's root or one it would delete.

On notes' On ice panel, git_stack adds Untrack to a stack while git-stack tracks its
branches.

## Buttons

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/git_stack/untrack` | `targets` (lines of `<repo dir>\t<branch>`) | `git stack untrack --json` for each repo's, saying which branches still stacked on them now start their own stacks. |
| `/actions/git_stack/stack_clean` | `repo` | `git stack clean -d local --json`, saying which branches it deleted, which it untracked but kept, and its warnings, or, when it fails, what it did first. |
