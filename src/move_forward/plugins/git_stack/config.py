"""`[git_stack]`."""

from pydantic import Field

from ...strict import Strict


class GitStackConfig(Strict):
    """`[git_stack]`: the section turns the plugin on for git's repos."""

    refresh_seconds: int = Field(default=300, ge=10)
    """How often it refreshes regardless. It also refreshes as soon as git's branches or
    git-stack's own record of the stacks move, which it checks after every refresh of
    git."""
