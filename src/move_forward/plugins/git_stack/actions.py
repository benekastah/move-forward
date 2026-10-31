"""The git-stack plugin's buttons: `git stack clean`, and untracking iced branches.

The repo must be one git has branches in, i.e. one of `[git] repos`.
"""

from pathlib import Path

from ...graph import Graph
from ...plugin import ActionError, Context
from ...shell import git
from .config import GitStackConfig
from .panels import BRANCH, BRANCH_ID_SEPARATOR, ICED_BRANCH, repo_dirs

STACK_CLEAN_TIMEOUT_SECONDS = 600
"""`git stack clean` fetches and checks every stack, which takes a while in a big repo."""


def stack_clean(ctx: Context[GitStackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack clean -d local`: untrack merged branches in every stack and delete them.

    Refused while the repo has iced branches, since clean touches every stack (the skill's
    rule).
    """
    if fields["repo"] not in repo_dirs(graph):
        raise ActionError(f"{fields['repo']} is not a configured repo")
    repo = Path(fields["repo"])
    iced = sorted(
        str(e.attrs.get("stack") or e.ref.id)
        for e in graph.of_kind(ICED_BRANCH)
        if Path(str(e.attrs.get("repo_dir", ""))) == repo
    )
    if iced:
        names = ", ".join(dict.fromkeys(iced))
        raise ActionError(f"{repo} has iced stacks ({names}); clean per stack instead")
    out = git(repo, "stack", "clean", "-d", "local", timeout=STACK_CLEAN_TIMEOUT_SECONDS)
    deleted = sum(1 for line in out.splitlines() if "deleted" in line.lower())
    return f"ran git stack clean -d local in {repo}" + (f", {deleted} deleted" if deleted else "")


def untrack(ctx: Context[GitStackConfig], graph: Graph, fields: dict[str, str]) -> str:
    """`git stack untrack` the `<repo>\t<branch>` lines of `targets`, repo by repo. Each must
    be a branch git-stack tracks: `git stack untrack` takes globs too, and they'd match more."""
    by_repo: dict[str, list[str]] = {}
    for line in fields.get("targets", "").splitlines():
        repo_dir, _, branch = line.partition(BRANCH_ID_SEPARATOR)
        if not branch:
            continue
        entity = graph.get(BRANCH, line)
        if entity is None or not entity.attrs.get("stack_parent"):
            raise ActionError(f"git-stack doesn't track {branch} in {repo_dir}")
        by_repo.setdefault(repo_dir, []).append(branch)
    if not by_repo:
        raise ActionError("no branches to untrack")
    unknown = sorted(set(by_repo) - set(repo_dirs(graph)))
    if unknown:
        raise ActionError(f"{', '.join(unknown)} is not a configured repo")
    for repo_dir, branches in by_repo.items():
        git(Path(repo_dir), "stack", "untrack", *branches)
    return "untracked " + ", ".join(b for branches in by_repo.values() for b in branches)
