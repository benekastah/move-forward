"""The git_stack plugin's data: the branches git stack tracks."""

from ...api import Model


class StackBranch(Model):
    """One entry of `git stack info --json`."""

    name: str
    parent: str | None = None
    status: str | None = None
    group: str | None = None


class StackRepo(Model):
    dir: str
    branches: list[StackBranch]
    """Every branch git stack tracks in the repo, and the roots they're on."""


class GitStackSnapshot(Model):
    repos: list[StackRepo]
