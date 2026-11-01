"""The git-stack plugin's buttons: `git stack clean`, and untracking iced branches.

The repo must be one git has branches in, i.e. one of `[git] repos`.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ...graph import Graph
from ...plugin import ActionError, Context
from ...shell import CommandError, git
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


class _Report(BaseModel):
    """The last line `git stack --json` prints: `result` or `error`, with every event it
    reported, so it alone says what happened."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    type: str
    message: str = ""
    """Why it failed, on an `error`."""
    events: list[_Event]


def report(out: str) -> _Report:
    """The report a `git stack ... --json` run ends with: it prints a JSON object per line,
    each event as it happens, then the report."""
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        raise ActionError("git stack printed no report; does its clean take --json?")
    try:
        return _Report.model_validate_json(lines[-1])
    except ValidationError as e:
        raise ActionError(f"couldn't read git stack's report: {e}") from e


def cleaned(events: list[_Event]) -> str:
    """What `git stack clean` did, in words: the branches deleted, those untracked but kept
    (checked out in another worktree, say), and its warnings."""
    untracked = [(e.branch, e.deleted_tip) for e in events if e.type == "untracked" and e.branch]
    deleted = [branch for branch, tip in untracked if tip]
    kept = [branch for branch, tip in untracked if not tip]
    parts = [f"deleted {', '.join(deleted)}"] if deleted else []
    parts += [f"untracked {', '.join(kept)}"] if kept else []
    parts += [f"warning: {e.message}" for e in events if e.warning]
    return "; ".join(parts)


def stack_clean(ctx: Context[GitStackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack clean -d local --json`: untrack merged branches in every stack and delete
    them, saying what it did, or how far it got before it failed.

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
    command = ["stack", "clean", "-d", "local", "--json"]
    try:
        out = git(repo, *command, timeout=STACK_CLEAN_TIMEOUT_SECONDS)
    except CommandError as e:
        if not e.stdout.strip():
            raise
        failed = report(e.stdout)
        done = cleaned(failed.events)
        raise ActionError(
            f"git stack clean failed: {failed.message or e}"
            + (f" (after it {done})" if done else "")
        ) from e
    return f"cleaned {repo}: {cleaned(report(out).events) or 'nothing merged to clean'}"


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
