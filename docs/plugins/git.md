# git

Local checkouts: each repo's worktrees and branches, and the directories I commit to.
Enabled by a `[git]` section.

## Config

`[git]` needs `repos`, your main checkouts; their other worktrees are found with
`git worktree list`, and a listed path that's a worktree of another listed repo is folded
into it. `my_branch_prefixes` marks branches as yours (without it every local branch is),
`ignored_worktree_globs` leaves worktrees out, and `commit_history_days` says how far back
your commits count towards the directories you work in.

## Publishes

`repo` for each checkout (by its GitHub `owner/name`) and `branch` for each local branch;
see [entities.md](../entities.md).

## Panels

- **Local work**: dirty worktrees, unpushed and never-pushed branches, with buttons: WIP
  commit (tracked files only), Push (never forced; sets the upstream for a new branch),
  and notes' Put on ice.
- **Cleanup**: local branches whose PR merged or whose remote is gone, with a Delete
  button each and Delete all (`git branch -D`; a branch checked out in a worktree is
  refused).

On notes' On ice panel, git adds Delete branches to a stack: the local ones, all or none,
after a confirmation; refused for a branch with commits since it was iced; remote branches
and PRs are untouched.

## Act now

git nominates the branches Cleanup lists, quick to delete.

## Buttons

`repo` is one of `[git] repos`, or any worktree of one.

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/git/push_branch` | `repo`, `branch` | `git push` (sets the upstream if missing; never forces). |
| `/actions/git/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`; the worktree must be the repo's. |
| `/actions/git/delete_branch` | `repo`, `branch` | `git branch -D`. Refused for a checked-out branch, or an iced one with commits since it was iced. |
| `/actions/git/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`) | Deletes each; one failure doesn't stop the rest. |
| `/actions/git/delete_together` | `targets` | Deletes all of them or, if any can't go, none. |
