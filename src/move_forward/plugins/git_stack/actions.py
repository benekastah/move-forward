"""The git-stack plugin's buttons: `git stack clean`, and untracking iced branches.

The repo must be one git has branches in, i.e. one of `[git] repos`.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from ...graph import Graph
from ...plugin import ActionError, Context
from ...shell import DEFAULT_TIMEOUT_SECONDS, CommandError, git
from .config import GitStackConfig
from .panels import STACK_BRANCH, StackBranchView, repo_dirs

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


class _Untracked(BaseModel):
    """A branch `git stack untrack` stopped tracking, of the fields read here."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    branch: str
    children: list[str] = []
    """Branches still stacked on it, which now start stacks of their own."""


class _Report(BaseModel):
    """The last line `git stack --json` prints: `result` or `error`, with every event it
    reported, so it alone says what happened, and the command's result."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    type: str
    message: str = ""
    """Why it failed, on an `error`."""
    events: list[_Event]
    untracked: list[_Untracked] = []
    """`untrack`'s result."""


def report(out: str) -> _Report:
    """The report a `git stack ... --json` run ends with: it prints a JSON object per line,
    each event as it happens, then the report."""
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        raise ActionError("git stack printed no report; does it take --json?")
    try:
        return _Report.model_validate_json(lines[-1])
    except ValidationError as e:
        raise ActionError(f"couldn't read git stack's report: {e}") from e


def run_json(repo: Path, args: list[str], done: Callable[[_Report], str], timeout: int) -> _Report:
    """Run `git stack <args> --json` in `repo` and return its report. When it fails, raise
    the reason as an `ActionError`, with `done` of what it reported before failing."""
    try:
        out = git(repo, "stack", *args, "--json", timeout=timeout)
    except CommandError as e:
        if not e.stdout.strip():
            raise
        failed = report(e.stdout)
        so_far = done(failed)
        raise ActionError(
            f"git stack {args[0]} failed: {failed.message or e}"
            + (f" (after it {so_far})" if so_far else "")
        ) from e
    return report(out)


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
    """`git stack clean -d local --json`: untrack merged branches in every stack, iced ones
    too, and delete them, saying what it did, or how far it got before it failed. It never
    restacks or pushes, so an iced stack's unmerged branches are left as they are, and a
    merged one it deletes can be restored at its iced commit by reopening the stack."""
    if fields["repo"] not in repo_dirs(graph):
        raise ActionError(f"{fields['repo']} is not a configured repo")
    repo = Path(fields["repo"])
    found = run_json(
        repo,
        ["clean", "-d", "local"],
        lambda r: cleaned(r.events),
        STACK_CLEAN_TIMEOUT_SECONDS,
    )
    return f"cleaned {repo}: {cleaned(found.events) or 'nothing merged to clean'}"


def untrack(ctx: Context[GitStackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack untrack --json` the `<repo>\t<branch>` lines of `targets`, repo by repo.
    Each must be a branch git-stack tracks: `git stack untrack` takes globs too, and they'd
    match more."""
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
    done: list[_Untracked] = []
    for repo_dir, branches in by_repo.items():
        done += run_json(
            Path(repo_dir), ["untrack", *branches], untracked, DEFAULT_TIMEOUT_SECONDS
        ).untracked
    return untracked_words(done)


def untracked(found: _Report) -> str:
    return untracked_words(found.untracked)


def untracked_words(done: list[_Untracked]) -> str:
    """What `git stack untrack` did: the branches it untracked, and those left stacked on
    them, which now start stacks of their own."""
    words = "untracked " + ", ".join(u.branch for u in done)
    gone = {u.branch for u in done}
    left = [child for u in done for child in u.children if child not in gone]
    if len(left) == 1:
        return f"{words}; {left[0]} now starts its own stack"
    return words + (f"; {', '.join(left)} now start stacks of their own" if left else "")
