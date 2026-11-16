"""Running git as the dashboard does, and finding a git checkout's repository.

`Git(repo)` is py-git-client's client as the dashboard configures it (its git, its timeout,
none started once it's stopping): run every git command through it, never py-git-client's own
`Git`. The rest is py-git-client's (its README says what each does), and what it raises."""

from git_client import GitError, config, config_key, refs

from move_forward.git import Git, git_common_dir

__all__ = ["Git", "GitError", "config", "config_key", "git_common_dir", "refs"]
