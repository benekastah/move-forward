import tomllib
from pathlib import Path

import pytest

from move_forward_dashboard import config, init


def scripted(answers: dict[str, str]) -> init.Prompt:
    """A prompt answering by a word of the question; anything unlisted takes the default."""

    def prompt(question: str, _default: str) -> str:
        return next((a for word, a in answers.items() if word in question), "")

    return prompt


@pytest.fixture
def no_machine_guesses(monkeypatch: pytest.MonkeyPatch) -> None:
    def guess_repo(_cwd: Path) -> tuple[Path | None, str]:
        return Path("/src/app"), "acme"

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
    with pytest.raises(ValueError, match="notes directory"):
        init.init(path, scripted({}), cwd=tmp_path)  # the only question with no guess
    message = init.init(path, scripted({"notes": "~/notes"}), cwd=tmp_path)
    assert message.startswith(f"wrote {path}")
    loaded = config.load(path)
    assert loaded.org == "acme" and loaded.team_slugs == ["core"]
    assert loaded.teams[0].slack_channel is None
    assert loaded.notes_dir == Path("~/notes").expanduser()
    assert loaded.repos == [Path("/src/app")]
    assert loaded.email_domain == "acme.com" and loaded.slack_workspace_url == ""
    assert loaded.my_branch_prefixes == [] and loaded.port == config.DEFAULT_PORT
    assert loaded.jira is None
    text = path.read_text()
    assert "# prune_days = 90" in text and "# [jira]" in text and "Jira" not in message


def test_init_takes_answers_and_sets_up_jira(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    message = init.init(
        path,
        scripted(
            {
                "organization": "other",
                "team slugs": "a, b ,",
                "pings to a": "#team-a",
                "notes": "~/notes",
                "checkouts": "~/x, ~/y",
                "workspace URL": "https://o.slack.com",
                "Prefixes": "jane/",
                "Port": "9000",
                "Jira site": "https://o.atlassian.net",
            }
        ),
        cwd=tmp_path,
    )
    loaded = config.load(path)
    assert loaded.org == "other" and loaded.team_slugs == ["a", "b"]
    assert [t.slack_channel for t in loaded.teams] == ["#team-a", None]
    assert loaded.notes_dir == Path("~/notes").expanduser()
    assert loaded.repos == [Path("~/x").expanduser(), Path("~/y").expanduser()]
    assert loaded.slack_workspace_url == "https://o.slack.com"
    assert loaded.my_branch_prefixes == ["jane/"] and loaded.port == 9000
    assert loaded.jira is not None
    assert (loaded.jira.site, loaded.jira.email) == ("https://o.atlassian.net", "jane@acme.com")
    assert "security add-generic-password -s move-forward-dashboard-jira" in message
    assert "# status_closed" in path.read_text()


def test_bad_answers_fail_before_writing(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError):
        init.init(path, scripted({"Port": "lots"}), cwd=tmp_path)
    assert not path.exists()


def test_rendered_config_is_valid_toml_with_every_optional_key_commented() -> None:
    text = init.render_config(
        {"org": "a", "repos": [], "notes_dir": "n"}, [{"github_slug": "t"}], None
    )
    assert tomllib.loads(text) == {
        "org": "a",
        "repos": [],
        "notes_dir": "n",
        "teams": [{"github_slug": "t"}],
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
        "slack_channel",
    }


def test_guesses_cope_without_git_or_gh(tmp_path: Path) -> None:
    assert init.guess_repo(tmp_path) == (None, "")
    assert init.guess_teams("") == [] and init.org_team_examples("") == []


def test_team_question_shows_the_orgs_teams_as_examples(no_machine_guesses: None) -> None:
    questions: list[str] = []

    def prompt(question: str, _default: str) -> str:
        questions.append(question)
        return "~/notes" if "notes" in question else ""

    init.interview(prompt, Path("/nowhere"))
    assert any("(e.g. platform, web)" in q for q in questions)
