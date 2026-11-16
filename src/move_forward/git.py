"""Running git as the dashboard does, through py-git-client, and finding a git checkout's
repository."""

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, override

import git_client
from git_client import CLIENT_TIMEOUT, Arg, EnvValue, GitError, Timeout

from move_forward.shell import DEFAULT_TIMEOUT_SECONDS, stopping

GIT_ENV = "MOVE_FORWARD_GIT"
"""The environment variable naming the git to run, in place of the one on PATH."""


class NotRunError(GitError):
    """git wasn't run: the dashboard is shutting down (see `shell.stop`)."""

    def __init__(self, args: Sequence[Arg]) -> None:
        super().__init__(args, None, "not run, shutting down")

    @override
    def __str__(self) -> str:
        return "git: not run, shutting down"


class Git(git_client.Git):
    """py-git-client's `Git`, as every git command of the dashboard's runs: the git
    `$MOVE_FORWARD_GIT` names, else the one on PATH (found once, at the first); each command
    stopped past `timeout` seconds, unless it's given its own; and, once `shell.stop` was
    called, none started at all, so a refresh in flight winds down fast.

    Raises FileNotFoundError when there's no git to run."""

    def __init__(self, directory: Path, *, timeout: float | None = DEFAULT_TIMEOUT_SECONDS):
        super().__init__(directory, env_var=GIT_ENV, timeout=timeout)

    @override
    def process(
        self,
        *args: Arg,
        capture_output: bool = False,
        input: bytes | None = None,
        stdout: int | IO[bytes] | IO[str] | None = None,
        env: Mapping[str, EnvValue] | None = None,
        timeout: Timeout = CLIENT_TIMEOUT,
    ) -> subprocess.CompletedProcess[bytes]:
        """As py-git-client's, but raising NotRunError, without starting git, once the
        dashboard is stopping."""
        if stopping():
            raise NotRunError(args)
        return super().process(
            *args,
            capture_output=capture_output,
            input=input,
            stdout=stdout,
            env=env,
            timeout=timeout,
        )

    def output(self, *args: Arg, timeout: Timeout = CLIENT_TIMEOUT) -> str:
        """git's stdout as it printed it, for a parser: not stripped, unlike `run`'s (a
        porcelain format's first line can start with a space, a file's name end with one),
        and with bytes that aren't UTF-8 replaced (U+FFFD) rather than kept as surrogates,
        which neither the database nor the page can take. Raises GitError as `run` does."""
        return self.run_bytes(*args, timeout=timeout).decode("utf-8", "replace")


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
