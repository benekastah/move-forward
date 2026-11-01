"""The entity kind git_stack publishes, with its attributes; see `docs/entities.md`."""

from ...domain import Model


class StackBranchAttrs(Model):
    """A `stack_branch`: a branch git-stack tracks, by its `branch` id, even after git has
    deleted it."""

    repo_dir: str
    name: str
    stack_parent: str
    """The branch it's built on."""
    stack_status: str | None
    """e.g. `up-to-date`, `needs-restack`, `merged`."""
    stack_group: str | None = None
    """On a stack's bottom branch (the one on its root), the group its stack is in, when
    the group has other stacks too. git-stack's commands (restack, sync, push) act on a
    branch's whole group, not just its stack."""
