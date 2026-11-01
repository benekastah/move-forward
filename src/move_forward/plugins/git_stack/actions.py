"""The git-stack plugin's buttons: `git stack clean`, and untracking iced branches.

The repo must be one git has branches in, i.e. one of `[git] repos`.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ...graph import Graph
from ...plugin import ActionError, Context
from ...shell import git
from .config import GitStackConfig
from .panels import STACK_BRANCH, StackBranchView, iced_stacks, repo_dirs

STACK_CLEAN_TIMEOUT_SECONDS = 600
"""`git stack clean` fetches and checks every stack, which takes a while in a big repo."""


class _Event(BaseModel):
    """One thing `git stack clean --json` reports having done, of the fields read here."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    type: str
    message: str
    warning: bool = False
    branch: str | None = None
    deleted_tip: str | None = None
    """On an `untracked` event, the commit the deleted branch was at; None if it was kept."""


class _CleanReport(BaseModel):
    """`git stack clean --json`'s output: what happened, in order."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    events: list[_Event]


def cleaned(out: str) -> str:
    """What `git stack clean --json` did, in words: the branches deleted, those untracked but
    kept (checked out in another worktree, say), and its warnings."""
    events = _CleanReport.model_validate_json(out).events
    untracked = [(e.branch, e.deleted_tip) for e in events if e.type == "untracked" and e.branch]
    deleted = [branch for branch, tip in untracked if tip]
    kept = [branch for branch, tip in untracked if not tip]
    parts = [f"deleted {', '.join(deleted)}"] if deleted else []
    parts += [f"untracked {', '.join(kept)}"] if kept else []
    parts += [f"warning: {e.message}" for e in events if e.warning]
    return "; ".join(parts) or "nothing merged to clean"


def stack_clean(ctx: Context[GitStackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack clean -d local`: untrack merged branches in every stack and delete them.

    Refused while the repo has iced branches, since clean touches every stack (the skill's
    rule).
    """
    if fields["repo"] not in repo_dirs(graph):
        raise ActionError(f"{fields['repo']} is not a configured repo")
    repo = Path(fields["repo"])
    iced = iced_stacks(graph).get(fields["repo"], [])
    if iced:
        names = ", ".join(sorted(iced))
        raise ActionError(f"{repo} has iced stacks ({names}); clean per stack instead")
    out = git(repo, "stack", "clean", "-d", "local", "--json", timeout=STACK_CLEAN_TIMEOUT_SECONDS)
    return f"cleaned {repo}: {cleaned(out)}"


def untrack(ctx: Context[GitStackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack untrack` the `<repo>\t<branch>` lines of `targets`, repo by repo. Each must
    be a branch git-stack tracks: `git stack untrack` takes globs too, and they'd match more."""
    by_repo: dict[str, list[str]] = {}
    for target in fields.get("targets", "").splitlines():
        if not target:
            continue
        tracked = graph.view_one(STACK_BRANCH, target, StackBranchView)
        if tracked is None:
            raise ActionError(f"git-stack doesn't track {target!r} in one of git's checkouts")
        by_repo.setdefault(tracked.repo_dir, []).append(tracked.name)
    if not by_repo:
        raise ActionError("no branches to untrack")
    unknown = sorted(set(by_repo) - set(repo_dirs(graph)))
    if unknown:
        raise ActionError(f"{', '.join(unknown)} is not a configured repo")
    for repo_dir, branches in by_repo.items():
        git(Path(repo_dir), "stack", "untrack", *branches)
    return "untracked " + ", ".join(b for branches in by_repo.values() for b in branches)
