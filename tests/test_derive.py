from pathlib import Path

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
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    local = derive.Checkout(github_repo="acme/widgets", dir=str(repo), default_branch="main")
    emails = {
        "alice": "alice@x",
        "bob": "bob@x",
        "carol": "carol@x",
        "me": "me@x",
    }
    suggestions = derive.suggest_reviewers(local, ["api/new.py"], 30, emails, exclude={"me"})
    assert [(s.login, s.commits) for s in suggestions] == [("alice", 2), ("bob", 1)]
