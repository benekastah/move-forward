"""`[git]`."""

from pathlib import Path

from pydantic import Field, field_validator

from ...strict import Strict

DEFAULT_COMMIT_HISTORY_DAYS = 180
DEFAULT_IGNORED_WORKTREE_GLOBS = ["*/.claude/worktrees/*"]


class GitConfig(Strict):
    """`[git]`: the repos I work in on this machine."""

    refresh_seconds: int = Field(default=60, ge=10)
    """How often the dashboard refreshes this plugin's data in the background (it only reads local checkouts)."""

    repos: list[Path]
    """Main checkouts of the repos I work in, one per repo. Their other worktrees are found
    from them with `git worktree list`, so those aren't listed."""
    my_branch_prefixes: list[str] = Field(default_factory=list)
    """Branch name prefixes that mark a branch as mine, e.g. `["jane/"]`. A branch with a PR
    of mine, or commits of mine off the default branch, is mine whatever its name."""
    ignored_worktree_globs: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IGNORED_WORKTREE_GLOBS)
    )
    """Worktrees to leave out, e.g. ones agents create for themselves."""
    commit_history_days: int = DEFAULT_COMMIT_HISTORY_DAYS
    """How far back my commits count towards "directories I work in"."""

    @field_validator("repos")
    @classmethod
    def _expand(cls, value: list[Path]) -> list[Path]:
        return [p.expanduser() for p in value]
