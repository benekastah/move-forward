"""The git plugin's buttons: delete, push and WIP-commit branches, and `git stack clean`.

Every action checks its repo is one of the configured checkouts and returns a one-line
notice for the page. Nothing here ever forces a push, resets, or stashes.
"""

from pathlib import Path

from ...graph import Graph
from ...plugin import ActionError, Context
from ...shell import CommandError, git
from . import client
from .config import GitConfig
from .panels import ICED_BRANCH, TARGET_SEPARATOR

STACK_CLEAN_TIMEOUT_SECONDS = 600
"""`git stack clean` fetches and checks every stack, which takes a while in a big repo."""
WIP_MESSAGE = "WIP"


def repo_path(config: GitConfig, repo_dir: str) -> Path:
    path = Path(repo_dir)
    if path not in config.repos:
        raise ActionError(f"{repo_dir} is not a configured repo")
    return path


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


def delete_branch(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    repo = repo_path(ctx.config, fields["repo"])
    branch = fields["branch"]
    if not branch_exists(repo, branch):
        raise ActionError(f"{branch} doesn't exist in {repo}")
    if worktree := checked_out_in(repo, branch):
        raise ActionError(f"{branch} is checked out in {worktree}; switch it away first")
    git(repo, "branch", "-D", branch)
    return f"deleted {branch} in {repo}"


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
    """Commit the modified tracked files of a worktree as "WIP". Untracked files are left."""
    repo_path(ctx.config, fields["repo"])
    worktree = Path(fields["worktree"])
    if not (worktree / ".git").exists():
        raise ActionError(f"{worktree} is not a worktree")
    modified, _ = client.parse_status(git(worktree, "status", "--porcelain"))
    if not modified:
        raise ActionError(f"{worktree} has no modified tracked files")
    git(worktree, "commit", "--all", "--quiet", "-m", WIP_MESSAGE)
    return f"committed {modified} files as {WIP_MESSAGE} in {worktree}"


def stack_clean(ctx: Context[GitConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack clean -d local`: untrack merged branches in every stack and delete them.

    Refused while the repo has iced branches, since clean touches every stack (the skill's
    rule).
    """
    repo = repo_path(ctx.config, fields["repo"])
    iced = sorted(
        str(e.attrs.get("stack") or e.ref.id)
        for e in graph.of_kind(ICED_BRANCH)
        if Path(str(e.attrs.get("repo_dir", ""))).expanduser() == repo
    )
    if iced:
        names = ", ".join(dict.fromkeys(iced))
        raise ActionError(f"{repo} has iced stacks ({names}); clean per stack instead")
    out = git(repo, "stack", "clean", "-d", "local", timeout=STACK_CLEAN_TIMEOUT_SECONDS)
    deleted = sum(1 for line in out.splitlines() if "deleted" in line.lower())
    return f"ran git stack clean -d local in {repo}" + (f", {deleted} deleted" if deleted else "")
