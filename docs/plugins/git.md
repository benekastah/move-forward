# git

Local checkouts: each repo's worktrees and branches, and the directories I commit to.
Enabled by a `[git]` section.

## Config

`refresh_seconds` (60) is how often it refreshes in the background: it only reads local checkouts.
It also refreshes within seconds of a commit, checkout, new or deleted branch, rebase,
push or fetch in one of the repos: every 5 seconds it compares the times of the files git
keeps its refs in, without running git. Edits not yet committed (and staging) wait for
`refresh_seconds`.

`[git]` needs `repos`, your main checkouts; their other worktrees are found with
`git worktree list`, and a listed path that's a worktree of another listed repo is folded
into it. A branch is yours if a PR of yours comes from it, if it has commits of yours
that aren't on the default branch (anywhere in its history, by the repo's `user.email`;
counted for branches active within `commit_history_days`), or if its name starts with one
of `my_branch_prefixes`. Without prefixes, a branch with only others' commits (a review
checkout) isn't yours, and an older one that wasn't counted is. `ignored_worktree_globs`
leaves worktrees out, and `commit_history_days` says how far back
your commits count towards the directories you work in.

Remotes can have any names. "The default branch" is the default branch on every remote
(`upstream/main` and `origin/main` in a fork), or the local one in a repo without remotes:
a commit on any of them is on the default branch. The repo's GitHub name comes from the
remote its local default branch tracks, else from the first remote on GitHub.

## Publishes

`repo` for each checkout (by its GitHub `owner/name`) and `branch` for each local branch;
see [entities.md](../entities.md).

## Panels

- **Local work**: dirty worktrees, unpushed and never-pushed branches, with buttons: WIP
  commit (tracked files only), Push (never forced; sets the upstream for a new branch),
  and notes' Put on ice. Also checkouts of open PRs (mine, or ones I review) that moved on
  GitHub: a branch with nothing to push whose PR's head, as github last saw it, is
  another commit. That's read from github's PRs, so it shows without fetching.
- **Cleanup**: local branches whose PR merged or whose remote is gone, with a Delete
  button each and Delete all (`git branch -D`; a branch checked out in a worktree is
  refused).

On notes' On ice panel, git adds Delete branches to a stack: the local ones, all or none,
after a confirmation; refused for a branch with commits since it was iced; remote branches
and PRs are untouched. Once they're gone, Restore branches recreates them at the commits
they were iced at (refused for one that exists again, or whose commit is gone).

## Act now

git nominates the branches Cleanup lists, quick to delete.

## Buttons

`repo` is one of `[git] repos`, or any worktree of one.

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/git/push_branch` | `repo`, `branch` | `git push`, where git would push it: a branch with an upstream to its push remote; one without to its `pushRemote`, `remote.pushDefault`, its remote or the only remote, setting the upstream (refused when there are several and nothing says which). Never forces. |
| `/actions/git/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`; the worktree must be the repo's. |
| `/actions/git/delete_branch` | `repo`, `branch` | `git branch -D`. Refused for a checked-out branch, or an iced one with commits since it was iced. |
| `/actions/git/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`) | Deletes each; one failure doesn't stop the rest. |
| `/actions/git/delete_together` | `targets` | Deletes all of them or, if any can't go, none. |
| `/actions/git/restore_together` | `targets` | Recreates each iced branch at the commit it was iced at: all of them or, if any can't be, none. |
