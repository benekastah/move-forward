"""`mfd init`'s questions for `[git]`."""

from ...api import Asker, InitSetup, branch_prefix, checkout, directories, path

EXAMPLE = '# repos = ["~/src/app", "~/src/infra"]\n'


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    """The repos, guessing the checkout init runs in, and my branch prefixes."""
    here = checkout(setup.cwd)
    repos = ask.list(
        "Main checkout of each repo you work in, comma-separated; other worktrees are found"
        " from it (e.g. ~/src/app, ~/src/infra)",
        [str(here)] if here else [],
        path,
        directories,
    )
    prefixes = ask.list(
        "Prefixes of your branch names (e.g. jane/; blank for none: your commits and PRs still"
        " mark branches as yours)",
        [],
        branch_prefix,
    )
    return {"repos": repos, **({"my_branch_prefixes": prefixes} if prefixes else {})}
