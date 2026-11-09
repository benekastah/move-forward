"""`[git]`."""

from pathlib import Path

from pydantic import Field, field_validator

from ...api import Strict

DEFAULT_REFRESH_SECONDS = 1800
DEFAULT_STATUS_SECONDS = 30
DEFAULT_COMMIT_HISTORY_DAYS = 180
DEFAULT_IGNORED_WORKTREE_GLOBS = ["*/.claude/worktrees/*"]


class GitConfig(Strict):
    """`[git]`: the repos I work in on this machine."""

    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=10)
    """How often the dashboard reads every repo anyway, a backstop: changes are seen as they
    happen (see `watch`)."""
    status_seconds: int = Field(default=DEFAULT_STATUS_SECONDS, ge=0)
    """For a worktree that can't be watched (see `watch`), how often its `git status` is
    checked for uncommitted edits, refreshing its repo when they changed; 0 leaves them to
    `refresh_seconds`."""

    repos: list[Path]
    """Main checkouts of the repos I work in, one per repo. Their other worktrees are found
    from them with `git worktree list`, so those aren't listed."""
    github_hosts: list[str] = Field(default_factory=lambda: ["github.com"])
    """Hosts whose remotes are GitHub repos, as remote URLs name them: github.com, a GitHub
    Enterprise Server's host, or an alias for either in ~/.ssh/config (`github-work` in
    `git@github-work:acme/widgets.git`)."""
    my_branch_prefixes: list[str] = Field(default_factory=list)
    """Branch name prefixes that mark a branch as mine, e.g. `["jane/"]`. A branch with a PR
    of mine, or commits of mine off the default branch, is mine whatever its name."""
    ignored_worktree_globs: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IGNORED_WORKTREE_GLOBS)
    )
    """Worktrees to leave out, e.g. ones agents create for themselves."""
    commit_history_days: int = DEFAULT_COMMIT_HISTORY_DAYS
    """How far back commits count, in days, e.g. `180`: mine towards "directories I work in",
    and others' towards github's reviewer suggestions."""

    @field_validator("repos")
    @classmethod
    def _expand(cls, value: list[Path]) -> list[Path]:
        return [p.expanduser() for p in value]
