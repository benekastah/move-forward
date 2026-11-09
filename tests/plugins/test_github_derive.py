from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import pr, snapshot

from move_forward.plugins.github import codeowners, derive
from move_forward.shell import git


def test_team_score_counts_owned_required_and_my_files() -> None:
    owners = codeowners.CodeOwners(
        "/api/ @acme/core #required\n/web/ @acme/web\n/docs/ @acme/core\n",
        required_marker="#required",
    )
    files = ["api/a.py", "api/b.py", "web/c.ts", "docs/d.md", "other/e"]
    score = derive.team_score(files, owners, {"@acme/core"}, my_dirs=["api", "other"])
    assert (score.owned_files, score.required_files, score.my_files) == (3, 2, 3)
    both = derive.team_score(files, owners, {"@acme/core", "@acme/web"}, my_dirs=[])
    assert both.owned_files == 4
    assert derive.team_score(["x"], None, {"@acme/core"}, []).owned_files == 0


def test_team_score_counts_the_part_owned_by_me_or_my_teams_with_its_lines() -> None:
    owners = codeowners.CodeOwners("/api/ @acme/core\n/web/ @me\n/docs/ @someone\n")
    files = ["api/a.py", "web/c.ts", "docs/d.md"]
    score = derive.team_score(files, owners, {"@acme/core"}, [], [10, 5, 100], "@me")
    assert (score.part_files, score.part_lines) == (2, 15)  # my team's and mine, not docs
    unowned = derive.team_score(["x"], owners, {"@acme/core"}, [], [7], "@me")
    assert (unowned.part_files, unowned.part_lines) == (0, 0)


def _commit(repo: Path, email: str, path: str) -> None:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(email + path)
    git(repo, "add", path)
    git(
        repo,
        "-c",
        f"user.email={email}",
        "-c",
        "user.name=x",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-q",
        "-m",
        path,
    )


def test_suggest_reviewers_counts_recent_committers_with_known_logins(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _commit(repo, "alice@x", "api/a.py")
    _commit(repo, "alice@x", "api/b.py")
    _commit(repo, "bob@x", "api/c.py")
    _commit(repo, "me@x", "api/d.py")
    _commit(repo, "carol@x", "web/e.py")
    _commit(repo, "nobody@x", "api/f.py")
    git(repo, "update-ref", "refs/remotes/upstream/main", "HEAD")
    local = derive.Checkout(
        github_repo="acme/widgets",
        dir=str(repo),
        default_branch="main",
        default_refs=["upstream/main"],
        commit_history_days=30,
    )
    emails = {
        "alice": "alice@x",
        "bob": "bob@x",
        "carol": "carol@x",
        "me": "me@x",
    }
    suggestions = derive.suggest_reviewers(local, ["api/new.py"], emails, exclude={"me"})
    assert [(s.login, s.commits) for s in suggestions] == [("alice", 2), ("bob", 1)]
    asked = derive.suggest_reviewers(local, ["api/new.py"], emails, exclude={"Me", "BOB"})
    assert [s.login for s in asked] == ["alice"]  # logins ignore case


def test_suggest_reviewers_counts_commits_within_the_checkouts_history_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    monkeypatch.setenv("GIT_COMMITTER_DATE", (datetime.now(UTC) - timedelta(days=60)).isoformat())
    _commit(repo, "alice@x", "api/a.py")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    _commit(repo, "bob@x", "api/b.py")
    git(repo, "update-ref", "refs/remotes/upstream/main", "HEAD")

    def suggested(days: int) -> list[str]:
        local = derive.Checkout(
            github_repo="acme/widgets",
            dir=str(repo),
            default_branch="main",
            default_refs=["upstream/main"],
            commit_history_days=days,
        )
        emails = {"alice": "alice@x", "bob": "bob@x"}
        return [s.login for s in derive.suggest_reviewers(local, ["api/x.py"], emails, set())]

    assert suggested(30) == ["bob"]  # alice's commit is older than git's window
    assert sorted(suggested(90)) == ["alice", "bob"]


def test_a_checkout_scores_its_prs_whatever_case_its_remote_names_the_repo_in(
    tmp_path: Path,
) -> None:
    checkout = derive.Checkout(
        github_repo="Acme/Widgets",
        dir=str(tmp_path),
        default_branch="main",
        my_dirs=["api"],
        commit_history_days=90,
    )
    asked = pr(7, author="alice", files=["api/v1/a.py", "web/b.ts"])
    assert asked.repo == "acme/widgets"
    found = derive.collect(snapshot(team_requests=[asked]), [checkout], {}, "acme", ["core"])
    assert found.team_scores[asked.url].my_files == 1  # in a dir I work in, below it


def test_each_of_my_prs_in_a_checkout_gets_suggestions_but_whom_it_asked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _commit(repo, "alice@x", "api/a.py")
    _commit(repo, "alice@x", "api/c.py")
    _commit(repo, "bob@x", "api/b.py")
    git(repo, "update-ref", "refs/remotes/upstream/main", "HEAD")
    local = derive.Checkout(
        github_repo="acme/widgets",
        dir=str(repo),
        default_branch="main",
        default_refs=["upstream/main"],
        commit_history_days=30,
    )
    unasked = pr(1, files=["api/x.py"])  # no reviewer requested, of no team
    asked_bob = pr(2, files=["api/x.py"], requested_users=["bob"])
    elsewhere = pr(3, files=["api/x.py"], repo="acme/other")  # no checkout of it
    theirs = pr(4, author="alice", files=["api/x.py"])
    github = snapshot(mine=[unasked, asked_bob, elsewhere], direct_requests=[theirs])
    emails = {"alice": "alice@x", "bob": "bob@x"}
    found = derive.collect(github, [local], emails, "acme", []).suggestions
    assert {url: [s.login for s in s_] for url, s_ in found.items()} == {
        unasked.url: ["alice", "bob"],
        asked_bob.url: ["alice"],
    }
    picked = derive.collect(
        github, [local], emails, "acme", [], wants_reviewer=lambda pr: pr.number == 2
    )
    assert list(picked.suggestions) == [asked_bob.url]  # only what wants_reviewer picks
