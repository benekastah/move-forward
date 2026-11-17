"""Running git as the dashboard does, through py-git-client, and finding a git checkout's
repository."""

from pathlib import Path

import git_client

from move_forward.shell import DEFAULT_TIMEOUT_SECONDS, stopping

GIT_ENV = "MOVE_FORWARD_GIT"
"""The environment variable naming the git to run, in place of the one on PATH."""


class Git(git_client.Git):
    """py-git-client's `Git`, as every git command of the dashboard's runs: the git
    `$MOVE_FORWARD_GIT` names, else the one on PATH (found once, at the first); each command
    stopped past `timeout` seconds, unless it's given its own (but a long-lived one, `popen`'s,
    which has none); and, once `shell.stop` was called, none started at all (each raises
    `NotStartedError`), so a refresh in flight winds down fast.

    Raises FileNotFoundError when there's no git to run."""

    def __init__(self, directory: Path, *, timeout: float | None = DEFAULT_TIMEOUT_SECONDS):
        super().__init__(directory, env_var=GIT_ENV, timeout=timeout, stopping=stopping)


def git_common_dir(repo: Path) -> Path | None:
    """The directory holding a checkout's refs and config: its `.git`, or for a linked
    worktree (whose `.git` is a file naming its own git directory) the main repository's.
    None when `repo` isn't a checkout. Read from the files, without running git."""
    dot = repo / ".git"
    if dot.is_dir():
        return dot
    if not dot.is_file():
        return None
    text = dot.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return None
    own = (repo / text.removeprefix("gitdir:").strip()).resolve()
    commondir = own / "commondir"
    return (
        (own / commondir.read_text(encoding="utf-8").strip()).resolve()
        if commondir.is_file()
        else own
    )
