"""Derived data that needs several sources: CODEOWNERS scores and reviewer suggestions."""

from collections import Counter
from pathlib import Path

from . import codeowners
from .domain import (
    DerivedSnapshot,
    GitHubSnapshot,
    LocalRepo,
    LocalSnapshot,
    ReviewerSuggestion,
    ReviewerUser,
    TeamPrScore,
)
from .shell import CommandError, git

MAX_SUGGESTIONS = 5


def _in_my_dirs(path: str, my_dirs: list[str]) -> bool:
    directory = str(Path(path).parent)
    return any(directory == d or directory.startswith(d + "/") for d in my_dirs if d != ".")


def team_score(
    files: list[str],
    owners: codeowners.CodeOwners | None,
    team_handles: set[str],
    my_dirs: list[str],
) -> TeamPrScore:
    """How much of a PR falls to my teams: `team_handles` are `@org/team` as CODEOWNERS has them."""
    owned = required = 0
    if owners:
        for path in files:
            path_owners, is_required = owners.owners(path)
            if team_handles.intersection(path_owners):
                owned += 1
                required += is_required
    return TeamPrScore(
        owned_files=owned,
        required_files=required,
        my_files=sum(_in_my_dirs(path, my_dirs) for path in files),
    )


def suggest_reviewers(
    repo: LocalRepo,
    files: list[str],
    since_days: int,
    users: dict[str, ReviewerUser],
    exclude: set[str],
) -> list[ReviewerSuggestion]:
    """People who recently committed to the PR's directories, by commit count.

    Only authors whose email is known (so they map to a GitHub login) are suggested.
    """
    dirs = sorted({str(Path(path).parent) for path in files}) or ["."]
    try:
        out = git(
            Path(repo.dir),
            "log",
            f"origin/{repo.default_branch}",
            f"--since={since_days} days ago",
            "--format=%ae",
            "--",
            *dirs,
        )
    except CommandError:
        return []
    by_email = Counter(out.split())
    login_by_email = {u.email: login for login, u in users.items() if u.email}
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
    local: LocalSnapshot,
    users: dict[str, ReviewerUser],
    org: str,
    teams: list[str],
    commit_history_days: int,
    required_owner_marker: str = "",
) -> DerivedSnapshot:
    repos = {r.github_repo: r for r in local.repos if r.github_repo}
    owners = {
        name: codeowners.load(Path(r.dir), required_owner_marker) for name, r in repos.items()
    }
    team_handles = {f"@{org}/{team}" for team in teams}

    scores = {
        pr.url: team_score(
            pr.files,
            owners.get(pr.repo),
            team_handles,
            repos[pr.repo].my_dirs if pr.repo in repos else [],
        )
        for pr in github.team_requests
    }
    suggestions = {
        pr.url: suggest_reviewers(
            repos[pr.repo], pr.files, commit_history_days, users, {github.login}
        )
        for pr in github.mine
        if pr.repo in repos
        and set(teams).intersection(pr.requested_teams)
        and not pr.requested_users
    }
    return DerivedSnapshot(suggestions=suggestions, team_scores=scores)
