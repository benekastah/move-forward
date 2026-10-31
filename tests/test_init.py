import tomllib
from pathlib import Path

import pytest

from move_forward_dashboard import config, init


def scripted(answers: dict[str, str | list[str]]) -> init.Prompt:
    """A prompt answering by a word of the question; anything unlisted takes the default.

    A list of answers is given one per asking, so a test can answer badly and then well.
    """
    queues = {word: list(a) if isinstance(a, list) else [a] for word, a in answers.items()}

    def prompt(question: str, _default: str) -> str:
        for word, queue in queues.items():
            if word in question:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return ""

    return prompt


@pytest.fixture
def no_machine_guesses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()

    def guess_repo(_cwd: Path) -> tuple[Path | None, str]:
        return tmp_path / "app", "acme"

    def guess_teams(org: str) -> list[str]:
        return ["core"] if org == "acme" else []

    def org_team_examples(org: str) -> list[str]:
        return ["platform", "web"] if org == "acme" else []

    monkeypatch.setattr(init, "guess_repo", guess_repo)
    monkeypatch.setattr(init, "guess_teams", guess_teams)
    monkeypatch.setattr(init, "org_team_examples", org_team_examples)
    monkeypatch.setattr(init, "guess_email", lambda: "jane@acme.com")


def test_init_takes_the_guesses_by_default(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "cfg" / "config.toml"
    message = init.init(path, scripted({}), cwd=tmp_path)
    assert message.startswith(f"wrote {path}")
    loaded = config.load(path)
    assert loaded.github is not None and loaded.git is not None
    assert loaded.github.org == "acme" and loaded.teams == ["core"]
    assert loaded.slack.teams == {}
    assert loaded.email == "jane@acme.com"
    assert loaded.notes_dir == config.DEFAULT_NOTES_DIR.expanduser()
    assert loaded.git.repos == [tmp_path / "app"]
    assert loaded.slack.workspace_url == ""
    assert loaded.git.my_branch_prefixes == [] and loaded.port == config.DEFAULT_PORT
    assert loaded.jira is None
    text = path.read_text()
    assert "# prune_days = 90" in text and "# [jira]" in text and "# [slack]" in text
    assert "# notes_dir" in text and "Jira" not in message


def test_init_takes_answers_and_sets_up_jira(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    message = init.init(
        path,
        scripted(
            {
                "organization": "other",
                "team slugs": "a, b ,",
                "pings to a": "#team-a",
                "checkout": f"{tmp_path}/x, {tmp_path}/y",
                "workspace URL": "https://o.slack.com",
                "Prefixes": "jane/",
                "Port": "9000",
                "Jira site": "https://o.atlassian.net",
                "work email": "jane@other.com",
            }
        ),
        cwd=tmp_path,
    )
    loaded = config.load(path)
    assert loaded.github is not None and loaded.git is not None
    assert loaded.github.org == "other" and loaded.teams == ["a", "b"]
    assert loaded.slack.teams == {"a": "#team-a"}
    assert loaded.email == "jane@other.com"
    assert loaded.notes_dir == config.DEFAULT_NOTES_DIR.expanduser()
    assert loaded.git.repos == [tmp_path / "x", tmp_path / "y"]
    assert loaded.slack.workspace_url == "https://o.slack.com"
    assert loaded.git.my_branch_prefixes == ["jane/"] and loaded.port == 9000
    assert loaded.jira is not None and loaded.jira.site == "https://o.atlassian.net"
    assert "security add-generic-password -s move-forward-jira" in message
    assert "# status_closed" in path.read_text()


def test_bad_answers_are_asked_again_with_the_reason(
    tmp_path: Path, no_machine_guesses: None, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    init.init(
        path,
        scripted(
            {
                "organization": ["acme corp", "acme"],
                "Port": ["lots", "70000", "9000"],
                "workspace URL": ["acme.slack.com", "https://acme.slack.com"],
            }
        ),
        cwd=tmp_path,
    )
    out = capsys.readouterr().out
    assert "'acme corp' isn't a GitHub organization" in out
    assert "'lots' isn't a port number" in out and "'70000' isn't a port number" in out
    assert "'acme.slack.com' isn't a URL starting with https://" in out
    loaded = config.load(path)
    assert loaded.github is not None and loaded.github.org == "acme" and loaded.port == 9000
    assert loaded.slack.workspace_url == "https://acme.slack.com"


def test_giving_up_after_repeated_bad_answers(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="no usable answer"):
        init.init(path, scripted({"Port": "lots"}), cwd=tmp_path)
    assert not path.exists()


def test_repo_paths_must_exist(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    init.init(
        path,
        scripted({"checkout": [f"{tmp_path}/nope", f"{tmp_path}/app"]}),
        cwd=tmp_path,
    )
    git = config.load(path).git
    assert git is not None and git.repos == [tmp_path / "app"]


def test_rendered_config_is_valid_toml_with_every_optional_key_commented() -> None:
    text = init.render_config(
        {"email": "e@x.y", "teams": ["t", "u"]},
        {"org": "a"},
        {"repos": []},
        None,
        {"t": "#t"},
        None,
    )
    assert tomllib.loads(text) == {
        "email": "e@x.y",
        "teams": ["t", "u"],
        "github": {"org": "a"},
        "git": {"repos": []},
        "slack": {"teams": {"t": "#t"}},
    }
    commented = {
        line[2:].split(" =")[0]
        for line in text.splitlines()
        if line.startswith("# ") and " = " in line and not line.startswith("# [")
    }
    assert commented >= {
        "db_path",
        "refresh_seconds",
        "ping_first",
        "prune_days",
        "status_closed",
        "required_owner_marker",
        "ticket_link_prefix",
        "commit_history_days",
        "stale_review_hours",
    }


def test_guesses_cope_without_git_or_gh(tmp_path: Path) -> None:
    assert init.guess_repo(tmp_path) == (None, "")
    assert init.guess_teams("") == [] and init.org_team_examples("") == []


def test_team_question_shows_the_orgs_teams_as_examples(no_machine_guesses: None) -> None:
    questions: list[str] = []

    def prompt(question: str, _default: str) -> str:
        questions.append(question)
        return ""

    init.interview(prompt, Path("/nowhere"))
    assert any("(e.g. platform, web)" in q for q in questions)
