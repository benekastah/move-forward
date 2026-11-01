"""What git_stack adds to git's `branch` entities; see `docs/entities.md`."""

from ...domain import Model


class StackBranchAttrs(Model):
    """A `branch` git-stack tracks."""

    stack_parent: str
    """The branch it's built on."""
    stack_status: str | None
    """e.g. `up-to-date`, `needs-restack`, `merged`."""
