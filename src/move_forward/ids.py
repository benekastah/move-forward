"""Ids several plugins build and read, in one format: a branch's, `<repo dir>\t<name>`."""

BRANCH_SEPARATOR = "\t"
"""Between a branch's checkout and its name in its id: a tab, which neither a path nor a
branch name has."""


def branch_id(repo_dir: str, name: str) -> str:
    """The id of the branch `name` in the checkout at `repo_dir`: a `git.branch` entity's."""
    return f"{repo_dir}{BRANCH_SEPARATOR}{name}"


def branch_parts(id: str) -> tuple[str, str]:
    """The checkout and the name a branch id (`branch_id`) holds."""
    repo_dir, _, name = id.partition(BRANCH_SEPARATOR)
    return repo_dir, name
