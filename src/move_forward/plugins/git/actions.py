"""The git plugin's buttons: delete, push and WIP-commit branches.

Every action checks its repo is one of the configured checkouts and returns a one-line
notice for the page. Nothing here ever forces a push, resets, or stashes.
"""

from pathlib import Path

from pydantic import Field

from ...domain import Model
from ...graph import Graph
from ...plugin import ActionError, Context
from ...shell import CommandError, git
from . import client
from .config import GitConfig
from .panels import ICED, TARGET_SEPARATOR, branch_ref

WIP_MESSAGE = "WIP"


class _IcedView(Model):
    """What git reads of an `iced` (notes'): the commits its branches were iced at."""

    shas: dict[str, str] = Field(default_factory=dict[str, str])


def repo_path(config: GitConfig, repo_dir: str) -> Path:
    path = Path(repo_dir)
    if path not in config.repos:
        raise ActionError(f"{repo_dir} is not a configured repo")
    return path


def worktrees(repo: Path) -> list[Path]:
    """The repo's worktrees, as `git worktree list` has them now."""
    return [
        Path(path)
        for path, _ in client.parse_worktrees(git(repo, "worktree", "list", "--porcelain"))
    ]


def branch_exists(repo: Path, branch: str) -> bool:
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except CommandError:
        return False
    return True


def checked_out_in(repo: Path, branch: str) -> str | None:
    for path, checked_out in client.parse_worktrees(git(repo, "worktree", "list", "--porcelain")):
        if checked_out == branch:
            return path
    return None


def _check_deletable(graph: Graph, repo: Path, branch: str) -> None:
    """Raise unless the branch can go: it exists, isn't checked out, and if it's on ice, has
    no commits since it was iced (they'd be lost with it)."""
    if not branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    if worktree := checked_out_in(repo, branch):
        raise ActionError(f"{branch} is checked out in {worktree}; switch it away first")
    ref = branch_ref(str(repo), branch)
    iced = graph.view_related(ref, ICED, _IcedView)
    iced_sha = next((i.shas[ref.id] for i in iced if ref.id in i.shas), None)
    if iced_sha and git(repo, "rev-parse", branch).strip() != iced_sha:
        raise ActionError(f"{branch} has commits since it was iced; thaw it, or ice it again")


def delete_branch(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    repo = repo_path(ctx.config, fields["repo"])
    branch = fields["branch"]
    _check_deletable(graph, repo, branch)
    git(repo, "branch", "-D", branch)
    return f"deleted {branch} in {repo}"


def delete_together(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Delete every `<repo>\t<branch>` line of `targets`, or none: each is checked first, so
    one that can't go stops it before anything is deleted. For a stack on ice."""
    targets: list[tuple[Path, str]] = []
    for line in fields.get("targets", "").splitlines():
        repo_dir, _, branch = line.partition(TARGET_SEPARATOR)
        if branch:
            targets.append((repo_path(ctx.config, repo_dir), branch))
    if not targets:
        raise ActionError("no branches to delete")
    for repo, branch in targets:
        _check_deletable(graph, repo, branch)
    for repo, branch in targets:
        git(repo, "branch", "-D", branch)
    return f"deleted {', '.join(branch for _, branch in targets)}"


def delete_branches(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Delete every `<repo>\\t<branch>` line of `targets`; one failure doesn't stop the rest."""
    done: list[str] = []
    failed: list[str] = []
    for line in fields.get("targets", "").splitlines():
        repo_dir, _, branch = line.partition(TARGET_SEPARATOR)
        if not branch:
            continue
        try:
            delete_branch(ctx, graph, {"repo": repo_dir, "branch": branch})
            done.append(branch)
        except (ActionError, CommandError) as e:
            failed.append(f"{branch}: {e}")
    notice = f"deleted {len(done)} branches"
    if failed:
        notice += "; failed: " + "; ".join(failed)
    return notice


def push_branch(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Push a branch, setting its upstream if it has none. Never forces."""
    repo = repo_path(ctx.config, fields["repo"])
    branch = fields["branch"]
    if not branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"{branch}@{{upstream}}")
        has_upstream = True
    except CommandError:
        has_upstream = False
    if has_upstream:
        git(repo, "push", "origin", f"{branch}:{branch}")
    else:
        git(repo, "push", "--set-upstream", "origin", branch)
    return f"pushed {branch}"


def wip_commit(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    """Commit the modified tracked files of a worktree of the repo as "WIP". Untracked files
    are left."""
    repo = repo_path(ctx.config, fields["repo"])
    worktree = Path(fields["worktree"])
    if worktree.resolve() not in {w.resolve() for w in worktrees(repo)}:
        raise ActionError(f"{worktree} is not a worktree of {repo}")
    modified, _ = client.parse_status(git(worktree, "status", "--porcelain"))
    if not modified:
        raise ActionError(f"{worktree} has no modified tracked files")
    git(worktree, "commit", "--all", "--quiet", "-m", WIP_MESSAGE)
    return f"committed {modified} files as {WIP_MESSAGE} in {worktree}"
