"""`mfd init`'s questions for `[git]`."""

from ...api import Asker, InitSetup, branch_prefix, checkout, directories, path

TEMPLATE = """\
# refresh_seconds = 1800               # how often it reads every repo anyway (it watches them)
# status_seconds = 30                  # how often edits are polled where watching fails (0: off)
# ignored_worktree_globs = ["*/.claude/worktrees/*"]
# commit_history_days = 180              # how far back commits count: my directories, reviewer suggestions
# my_branch_prefixes = []               # besides branches with my commits or PRs, e.g. ["me/"]
# github_hosts = ["github.com"]          # remote hosts that are GitHub: an Enterprise server, ~/.ssh/config aliases
"""


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
