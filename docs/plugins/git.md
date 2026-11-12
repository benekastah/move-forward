# git

Local checkouts: each repo's worktrees and branches, and the directories I commit to.
Enabled by a `[git]` section.

## Config

`refresh_seconds` (1800) is how often it reads every repo anyway, as a backstop. Otherwise
it refreshes within seconds of a change: it watches each repo (with watchfiles: inotify
on Linux, FSEvents on macOS, `ReadDirectoryChangesW` on Windows), so a commit, checkout,
new or deleted branch, rebase, push or fetch, a change to its config (an upstream set,
git stack tracking a branch), staging, or an edit that changes what `git status` counts,
refreshes the repo it happened in, reading only that one.

It watches each repo's git directory, and each worktree where git tracks files: on Linux
each directory in the index, on its own, so ignored trees (`node_modules`, `build`,
`.venv`, wherever they are) aren't watched; on macOS and Windows the worktree, as a tree.
On Linux each watched directory takes an inotify watch, counted against
`fs.inotify.max_user_watches`. What it can't watch (over that limit, say), or a watch
that stopped on an error until the next refresh watches it anew, is polled instead, as it
says in the log: refs and config every 2 seconds by the times of the files
git keeps them in, without running git, and edits every `status_seconds` (30; 0 leaves
them to `refresh_seconds`) by `git status`. `git status` takes no lock, so it never gets in
the way of your own git commands; in a very large repo, git's own file watcher makes it
cheap: `git config core.fsmonitor true` (built in on macOS and Windows; on Linux it takes
Watchman) and `git config core.untrackedCache true`.

`[git]` needs `repos`, your main checkouts; their other worktrees are found with
`git worktree list`, and a listed path that's a worktree of another listed repo is folded
into it. Two separate clones of one GitHub repo are a problem on Git's group: only the branches
of the one listed first in `repos` meet its PRs, so make the others worktrees of it. A branch is yours if a PR of yours comes from it, if it has commits of yours
that aren't on the default branch (anywhere in its history, by the repo's `user.email`;
counted for branches active within `commit_history_days`), or if its name starts with one
of `my_branch_prefixes`. Without prefixes, a branch with only others' commits (a review
checkout) isn't yours, and an older one that wasn't counted is. Local work also shows
the unpushed commits of any branch git stack tracks (with the git_stack plugin), since
stacks are built on your own branches. `ignored_worktree_globs`
leaves worktrees out, and `commit_history_days` (180) says how far back commits count: yours
(by the repo's `user.email`) towards the directories you work in, and others' towards the
github plugin's reviewer suggestions. A
repo git can't read (one whose git hangs, say) is logged and kept as it was last read, or
left out if it never was (a path that isn't a repo), so it doesn't hold up the others,
until its next change or the next full read (every `refresh_seconds`); a worktree deleted
without `git worktree remove` is left out.

Remotes can have any names. "The default branch" is the default branch on every remote
(`upstream/main` and `origin/main` in a fork): a commit on any of them is on the default
branch. When no remote's `HEAD` is known (a repo without remotes, or one pushed to without
`git remote set-head`), it's `init.defaultBranch`, `main` or `master`, the first the repo has,
on each remote that has it, else the local one; with none of those, no branch is counted
against it, so none shows as never pushed. The repo's GitHub name comes from the
remote its local default branch tracks, else from the first remote on GitHub. A remote is on
GitHub when its host, as the remote URL names it, is one of `github_hosts`
(`["github.com"]`): add a GitHub Enterprise Server's host, or an alias from
`~/.ssh/config` (`github-work` in `git@github-work:acme/widgets.git`).

## Publishes

`git.repo` for each GitHub repo checked out (by its `owner/name`; of two clones of it, the
one listed first in `repos`) and `git.branch` for each local branch;
see [entities.md](../entities.md).

## Panels

- **Local work**: dirty worktrees, unpushed and never-pushed branches, with buttons: WIP
  commit (tracked files only), Push (never forced; sets the upstream for a new branch),
  and notes' Put on ice. A branch is one item, with both buttons when it has uncommitted
  changes and unpushed commits. A branch behind its upstream (say after a rebase) has no
  Push, since a plain push would be rejected, and says so instead. Also checkouts of open PRs (mine, or ones I review) that moved on
  GitHub: a branch with nothing to push whose PR's head, as github last saw it, is
  another commit. That's read from github's PRs, so it shows without fetching; its Pull fetches
  and fast-forwards the branch, refused when the PR was force-pushed or the checkout has
  changes in the way.
- **Cleanup**: local branches whose PR merged or whose remote is gone, with a Delete
  button each and Delete all (`git branch -D`; a branch checked out in a worktree is
  refused). The default branch is never listed. A branch with work that may be its only copy
  is listed "check first", last, saying why, and left out of Delete all and Act now, its Delete
  warning that the commits go with it: one whose PR merged but that points at another commit
  than the one that merged, and one whose remote is gone with commits not on the default branch
  (or where that isn't known). Delete and Delete all are refused for a branch that moved since
  the page showed it. A branch git stack sees merged is left to git_stack's Clean, which untracks it
  as it deletes it. Delete is a 🗑️ icon, its label in its title; when notes also offers
  Put on ice, the two appear as one group. Delete still asks for confirmation.

On notes' On ice panel, git adds Delete branches to a stack: the local ones, all or none,
after a confirmation; refused for a branch with commits since it was iced; remote branches
and PRs are untouched. Once they're gone, Restore branches recreates them at the commits
they were iced at (refused for one that exists again, or whose commit is gone).

## Act now

git does not nominate branch cleanup for Act now; find these branches in Cleanup.

## Buttons

`repo` is one of `[git] repos`, or any worktree of one.

| Path | Fields | Does |
| --- | --- | --- |
| `/actions/git/pull_branch` | `repo`, `branch` | Fetches its upstream's remote and fast-forwards the branch to its upstream (for one tracking a local branch, its copy where Push sends it), checked out or not; refused when it has commits its upstream doesn't, or its checkout has uncommitted changes. Never merges or forces. |
| `/actions/git/push_branch` | `repo`, `branch` | `git push`, where git would push it: a branch with an upstream to its push remote; one without to its `pushRemote`, `remote.pushDefault`, its remote or the only remote, setting the upstream (refused when there are several and nothing says which). One tracking a local branch (`--track main`) is pushed to that remote under its own name, never into the local branch, and is counted against that copy once it's there. Never forces. |
| `/actions/git/wip_commit` | `repo`, `worktree` | Commits the worktree's modified tracked files as `WIP`; the worktree must be the repo's. |
| `/actions/git/delete_branch` | `repo`, `branch`, optional `sha` | `git branch -D`. Refused for a checked-out branch, an iced one with commits since it was iced, or one no longer at `sha` (the commit the page showed). |
| `/actions/git/delete_branches` | `targets` (lines of `<repo dir>\t<branch>`, optionally `\t<sha>`) | Deletes each, as `delete_branch` does; one failure doesn't stop the rest. |
| `/actions/git/delete_together` | `targets` | Deletes all of them or, if any can't go, none. |
| `/actions/git/restore_together` | `targets` | Recreates each iced branch at the commit it was iced at: all of them or, if any can't be, none. |

## Notifications

Subscribe under Notifications on `/settings` (or `[notifications] subscribe` as
`git.branch_deletable`). It says once that a local branch has joined the Cleanup panel
(its PR merged, or its remote branch is gone): said again if it leaves and comes back. The
first time it's asked, it only keeps what's there.

