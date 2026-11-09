"""Notification events of the git_stack plugin: branches that need a restack or were merged."""

from ...api import Event, anchor
from .models import GitStackSnapshot, StackBranch
from .panels import stack_branch_ref

KINDS = {
    "needs_restack": "a git stack branch needs a restack",
    "merged": "a git stack branch was merged: `git stack clean` can remove it",
}
"""The kinds, with what each says, for the settings page."""
NEEDS_RESTACK = "needs-restack"
MERGED = "merged"


def _event(repo: str, branch: StackBranch, kind: str, text: str) -> Event:
    ref = stack_branch_ref(repo, branch.name)
    return Event(
        kind=kind,
        thing=f"stack:{ref.id}",
        title=f"{branch.name} in {repo}",
        text=text,
        anchor=anchor(ref.kind, ref.id),
    )


def changes(before: GitStackSnapshot, after: GitStackSnapshot) -> list[Event]:
    """The branches whose status became `needs-restack` or `merged` since `before`; a branch
    it didn't hold says nothing."""
    old = {(repo.dir, b.name): b.status for repo in before.repos for b in repo.branches}
    found: list[Event] = []
    for repo in after.repos:
        for branch in repo.branches:
            key = (repo.dir, branch.name)
            if key not in old or old[key] == branch.status:
                continue
            if branch.status == NEEDS_RESTACK:
                found.append(_event(repo.dir, branch, "needs_restack", "needs a restack"))
            elif branch.status == MERGED:
                found.append(_event(repo.dir, branch, "merged", "was merged"))
    return found
