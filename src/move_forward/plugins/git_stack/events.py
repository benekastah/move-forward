"""Notification events of the git_stack plugin: branches that need a restack or were merged,
as change rules over its `stack_branch`es (`Plugin.change_rules`)."""

from ...api import ChangeRule, Model, becomes
from .panels import STACK_BRANCH

KINDS = {
    "needs_restack": "a git stack branch needs a restack",
    "merged": "a git stack branch was merged: `git stack clean` can remove it",
}
"""The kinds, with what each says, for the settings page."""
NEEDS_RESTACK = "needs-restack"
MERGED = "merged"


class _BranchView(Model):
    """What the rules read of a `stack_branch`."""

    id: str
    repo_dir: str
    name: str
    stack_status: str | None


def _rule(kind: str, status: str, text: str) -> ChangeRule[_BranchView]:
    """`kind` when a branch's status becomes `status`."""
    return ChangeRule(
        kind=kind,
        entity=STACK_BRANCH,
        view=_BranchView,
        when=becomes(lambda b: b.stack_status == status),
        text=text,
        thing=lambda b: f"stack:{b.id}",
        title=lambda b: f"{b.name} in {b.repo_dir}",
    )


RULES = [
    _rule("needs_restack", NEEDS_RESTACK, "needs a restack"),
    _rule("merged", MERGED, "was merged"),
]
