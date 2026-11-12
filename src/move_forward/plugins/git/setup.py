"""`mfd init`'s questions for `[git]`."""

from ...api import Asker, Init, InitSection, InitSetup, branch_prefix, checkout, directories, path

EXAMPLE = InitSection({"repos": ["~/src/app", "~/src/infra"]})


def questions(ask: Asker, setup: InitSetup) -> InitSection:
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
    return InitSection({"repos": repos, **({"my_branch_prefixes": prefixes} if prefixes else {})})


INIT = Init(questions=questions, example=EXAMPLE, suggested=True, example_on=True)
"""Always suggested: init guesses its repo from the checkout it runs in."""
