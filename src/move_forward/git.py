"""Running git as the dashboard does, through py-git-client."""

from pathlib import Path

import git_client

from move_forward.shell import DEFAULT_TIMEOUT_SECONDS, stopping

GIT_ENV = "MOVE_FORWARD_GIT"
"""The environment variable naming the git to run, in place of the one on PATH."""


class Git(git_client.Git):
    """py-git-client's `Git`, as every git command of the dashboard's runs: the git
    `$MOVE_FORWARD_GIT` names, else the one on PATH (found once, at the first); each command
    stopped past `timeout` seconds, unless it's given its own. `popen` itself has no timeout,
    but `CatFile` limits each request. Once `shell.stop` was called, new Git processes raise
    `NotStartedError` before starting, so a refresh in flight winds down fast.

    Raises FileNotFoundError when there's no git to run."""

    def __init__(
        self,
        directory: Path,
        *,
        timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
        cache: git_client.GitCache | None = None,
    ) -> None:
        super().__init__(
            directory, env_var=GIT_ENV, timeout=timeout, stopping=stopping, cache=cache
        )
