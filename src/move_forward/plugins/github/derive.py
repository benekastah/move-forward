"""Derived data that needs local checkouts: CODEOWNERS scores and reviewer suggestions."""

from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from pydantic import Field

from ...api import Model
from ...api.shell import CommandError, git
from . import codeowners
from .models import DerivedSnapshot, GitHubSnapshot, PullRequest, ReviewerSuggestion, TeamPrScore

MAX_SUGGESTIONS = 5


class Checkout(Model):
    """A local checkout of a GitHub repo, from the graph's `git.repo` entities."""

    github_repo: str
    """owner/name."""
    dir: str
    default_branch: str
    default_refs: list[str] = Field(default_factory=list)
    """The default branch on each of its remotes, as refs `git log` takes in `dir`."""
    my_dirs: list[str] = Field(default_factory=list)
    """Directories I committed to recently."""


def _in_my_dirs(path: str, my_dirs: list[str]) -> bool:
    directory = str(PurePosixPath(path).parent)  # git's and GitHub's paths, on every system
    return any(directory == d or directory.startswith(d + "/") for d in my_dirs if d != ".")


def team_score(
    files: list[str],
    owners: codeowners.CodeOwners | None,
    team_handles: set[str],
    my_dirs: list[str],
    lines: list[int] | None = None,
    my_handle: str | None = None,
) -> TeamPrScore:
    """How much of a PR falls to my teams: `team_handles` are `@org/team` as CODEOWNERS has them.

    `lines` are each file's changed lines. With them, and `my_handle` (`@login`), the part
    owned by me or my teams is counted too (`part_files`, `part_lines`).
    """
    owned = required = part_files = part_lines = 0
    mine: set[str] = {*team_handles, *([my_handle] if my_handle else [])}
    if owners:
        for index, path in enumerate(files):
            path_owners, is_required = owners.owners(path)
            if team_handles.intersection(path_owners):
                owned += 1
                required += is_required
            if mine.intersection(path_owners):
                part_files += 1
                part_lines += lines[index] if lines and index < len(lines) else 0
    return TeamPrScore(
        owned_files=owned,
        required_files=required,
        my_files=sum(_in_my_dirs(path, my_dirs) for path in files),
        part_files=part_files,
        part_lines=part_lines,
    )


def suggest_reviewers(
    repo: Checkout,
    files: list[str],
    since_days: int,
    emails: Mapping[str, str | None],
    exclude: set[str],
) -> list[ReviewerSuggestion]:
    """People who recently committed to the PR's directories, by commit count.

    Only authors whose email is known (so they map to a GitHub login) are suggested;
    `emails` are by login.
    """
    dirs = sorted({str(PurePosixPath(path).parent) for path in files}) or ["."]
    if not repo.default_refs:
        return []
    try:
        out = git(
            Path(repo.dir),
            "log",
            *repo.default_refs,
            f"--since={since_days} days ago",
            "--format=%ae",
            "--",
            *dirs,
        )
    except CommandError:
        return []
    by_email = Counter(out.split())
    login_by_email = {email: login for login, email in emails.items() if email}
    counts: Counter[str] = Counter()
    for email, count in by_email.items():
        login = login_by_email.get(email)
        if login and login not in exclude:
            counts[login] += count
    return [
        ReviewerSuggestion(login=login, commits=count)
        for login, count in counts.most_common(MAX_SUGGESTIONS)
    ]


def collect(
    github: GitHubSnapshot,
    checkouts: list[Checkout],
    emails: Mapping[str, str | None],
    org: str,
    teams: list[str],
    commit_history_days: int,
    required_owner_marker: str = "",
    wants_reviewer: Callable[[PullRequest], bool] = lambda pr: True,
) -> DerivedSnapshot:
    """`teams` are slugs, as PRs' requested teams have them. Reviewers are suggested for my
    PRs `wants_reviewer` says a reviewer may yet be picked for, each a `git log`. A checkout's repo is matched to a
    PR's whatever case the remote names it in, as GitHub's names ignore case."""
    repos = {r.github_repo.lower(): r for r in checkouts}
    owners = {
        name: codeowners.load(Path(r.dir), required_owner_marker) for name, r in repos.items()
    }
    team_handles = {f"@{org}/{team}" for team in teams}

    asked = {
        pr.url: pr
        for pr in (*github.team_requests, *github.direct_requests, *github.reviewed_by_me)
    }
    scores = {
        pr.url: team_score(
            pr.files,
            owners.get(pr.repo.lower()),
            team_handles,
            repos[pr.repo.lower()].my_dirs if pr.repo.lower() in repos else [],
            pr.file_lines,
            f"@{github.login}",
        )
        for pr in asked.values()
    }
    suggestions = {
        pr.url: suggest_reviewers(
            repos[pr.repo.lower()],
            pr.files,
            commit_history_days,
            emails,
            {github.login, *pr.requested_users},
        )
        for pr in github.mine
        if pr.repo.lower() in repos and wants_reviewer(pr)
    }
    return DerivedSnapshot(suggestions=suggestions, team_scores=scores)
