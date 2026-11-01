import tomllib
from pathlib import Path

import pytest

from move_forward import asking, config, init
from move_forward.plugins.git import setup as git_setup
from move_forward.plugins.git_stack.plugin import GitStackPlugin
from move_forward.plugins.github import setup as github_setup
from move_forward.shell import git


def scripted(answers: dict[str, str | list[str]]) -> asking.Prompt:
    """A prompt answering by a word of the question; anything unlisted takes the default.

    A list of answers is given one per asking, so a test can answer badly and then well.
    """
    queues = {word: list(a) if isinstance(a, list) else [a] for word, a in answers.items()}

    def prompt(question: str, _default: str, _complete: asking.Complete | None = None) -> str:
        for word, queue in queues.items():
            if word in question:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return ""

    return prompt


@pytest.fixture
def no_machine_guesses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()

    def checkout(_cwd: Path) -> Path | None:
        return tmp_path / "app"

    def guess_org(_cwd: Path) -> str:
        return "acme"

    def guess_teams(org: str) -> list[str]:
        return ["core"] if org == "acme" else []

    def org_team_examples(org: str) -> list[str]:
        return ["platform", "web"] if org == "acme" else []

    def installed(_self: GitStackPlugin) -> bool:
        return False

    monkeypatch.setattr(git_setup, "checkout", checkout)
    monkeypatch.setattr(github_setup, "guess_org", guess_org)
    monkeypatch.setattr(github_setup, "guess_teams", guess_teams)
    monkeypatch.setattr(github_setup, "org_team_examples", org_team_examples)
    monkeypatch.setattr(init, "guess_email", lambda: "jane@acme.com")
    monkeypatch.setattr(GitStackPlugin, "init_suggested", installed)


def test_init_takes_the_guesses_by_default(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "cfg" / "config.toml"
    message = init.init(path, scripted({}), cwd=tmp_path)
    assert message.startswith(f"wrote {path}")
    loaded = config.load(path)
    assert loaded.github is not None and loaded.git is not None
    assert loaded.github.org == "acme" and loaded.teams == ["core"]
    assert loaded.slack is None
    assert loaded.email == "jane@acme.com"
    assert loaded.git.repos == [tmp_path / "app"]
    assert loaded.git.my_branch_prefixes == [] and loaded.port == config.DEFAULT_PORT
    assert loaded.jira is None and loaded.git_stack is None
    text = path.read_text()
    assert "# prune_days = 90" in text and "# [jira]" in text and "# [slack]" in text
    assert "[notes]" not in text and "# [git_stack]" in text and "Jira" not in message


def test_init_turns_on_git_stack_when_it_is_installed(
    tmp_path: Path, no_machine_guesses: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def installed(_self: GitStackPlugin) -> bool:
        return True

    monkeypatch.setattr(GitStackPlugin, "init_suggested", installed)
    path = tmp_path / "config.toml"
    init.init(path, scripted({}), cwd=tmp_path)
    assert config.load(path).git_stack is not None


def test_init_takes_answers_and_sets_up_jira(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    message = init.init(
        path,
        scripted(
            {
                "Plugins": "github, git, jira, slack",
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
    assert loaded.slack is not None and loaded.slack.teams == {"a": "#team-a"}
    assert loaded.email == "jane@other.com"
    assert loaded.git.repos == [tmp_path / "x", tmp_path / "y"]
    assert loaded.slack.workspace_url == "https://o.slack.com"
    assert loaded.git.my_branch_prefixes == ["jane/"] and loaded.port == 9000
    assert loaded.jira is not None and loaded.jira.site == "https://o.atlassian.net"
    assert "`mfd token set jira`" in message
    assert "# status_closed" in path.read_text()


def test_bad_answers_are_asked_again_with_the_reason(
    tmp_path: Path, no_machine_guesses: None, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    init.init(
        path,
        scripted(
            {
                "Plugins": ["github, gitlab", "github, git_stack", "github, git, jira, slack"],
                "organization": ["acme corp", "acme"],
                "Port": ["lots", "70000", "9000"],
                "workspace URL": ["acme.slack.com", "https://acme.slack.com"],
                "Jira site": ["http://acme.atlassian.net", "https://acme.atlassian.net"],
            }
        ),
        cwd=tmp_path,
    )
    out = capsys.readouterr().out
    assert "no plugin named 'gitlab'" in out and "git_stack needs git too" in out
    assert "'acme corp' isn't a GitHub organization" in out
    assert "'lots' isn't a port number" in out and "'70000' isn't a port number" in out
    assert "'acme.slack.com' isn't a URL starting with https://" in out
    assert "'http://acme.atlassian.net' must start with https://" in out
    loaded = config.load(path)
    assert loaded.github is not None and loaded.github.org == "acme" and loaded.port == 9000
    assert loaded.slack is not None and loaded.slack.workspace_url == "https://acme.slack.com"
    assert loaded.jira is not None and loaded.jira.site == "https://acme.atlassian.net"


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
        {"github": {"org": "a"}, "git": {"repos": []}, "slack": {"teams": {"t": "#t"}}},
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
        "size",
        "per_plugin",
        "weights",
    }


def test_guesses_cope_without_git_or_gh(tmp_path: Path) -> None:
    assert asking.checkout(tmp_path) is None and github_setup.guess_org(tmp_path) == ""
    assert github_setup.guess_teams("") == [] and github_setup.org_team_examples("") == []


def test_the_org_is_guessed_from_the_remote_the_branch_tracks(tmp_path: Path) -> None:
    org = tmp_path / "org"
    git(tmp_path, "init", "-q", "-b", "main", str(org))
    git(
        org,
        "-c",
        "user.email=me@x",
        "-c",
        "user.name=Me",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "a",
    )
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", "-o", "upstream", str(org), str(repo))
    git(repo, "remote", "add", "a-fork", "https://github.com/me/widgets.git")
    assert github_setup.guess_org(repo) == "me"  # main tracks upstream, which isn't on GitHub
    git(repo, "remote", "set-url", "upstream", "git@github.com:acme/widgets.git")
    assert github_setup.guess_org(repo) == "acme"  # the tracked one first


def test_without_github_the_teams_are_asked_by_name(
    tmp_path: Path, no_machine_guesses: None
) -> None:
    path = tmp_path / "config.toml"
    answers: dict[str, str | list[str]] = {
        "Plugins": "git, slack",
        "Teams": "web, api",
        "pings to web": "#web",
    }
    init.init(path, scripted(answers), cwd=tmp_path)
    loaded = config.load(path)
    assert loaded.github is None and loaded.teams == ["web", "api"]
    assert loaded.slack is not None and loaded.slack.teams == {"web": "#web"}
    assert "# [github]" in path.read_text()


def test_team_question_shows_the_orgs_teams_as_examples(no_machine_guesses: None) -> None:
    questions: list[str] = []

    def prompt(question: str, _default: str, _complete: asking.Complete | None = None) -> str:
        questions.append(question)
        return ""

    init.interview(prompt, Path("/nowhere"))
    assert any("(e.g. platform, web)" in q for q in questions)


def test_a_pasted_org_url_gives_the_org(tmp_path: Path, no_machine_guesses: None) -> None:
    assert github_setup.org("https://github.com/acme/") == "acme"
    assert github_setup.org("github.com/acme") == "acme" and github_setup.org("acme") == "acme"
    with pytest.raises(ValueError, match="only github.com"):
        github_setup.org("https://github.example.com/acme")
    path = tmp_path / "config.toml"
    init.init(path, scripted({"organization": "https://github.com/other"}), cwd=tmp_path)
    github = config.load(path).github
    assert github is not None and github.org == "other"


def test_tab_completes_plugins_the_orgs_teams_and_directories(
    tmp_path: Path, no_machine_guesses: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[str] = []

    def org_teams(org: str) -> list[str]:
        fetched.append(org)
        return ["core", "core-infra", "web"]

    monkeypatch.setattr(github_setup, "org_teams", org_teams)
    completions: dict[str, list[str]] = {}

    def prompt(question: str, _default: str, complete: asking.Complete | None = None) -> str:
        if complete:
            word = question.split()[0]
            completions[word] = complete("co" if word == "GitHub" else "")
        return ""

    (tmp_path / "app" / "src").mkdir()
    (tmp_path / "app" / ".hidden").mkdir()
    init.interview(prompt, tmp_path)
    assert completions["Plugins"] == ["github", "git", "git_stack", "jira", "slack"]
    assert completions["GitHub"] == ["core", "core-infra"] and fetched == ["acme"]
    assert asking.directories(f"{tmp_path}/app/") == [f"{tmp_path}/app/src/"]
    assert asking.directories(f"{tmp_path}/ap") == [f"{tmp_path}/app/"]
    assert asking.directories(f"{tmp_path}/nope/x") == []


def test_teams_are_fetched_only_when_completion_asks() -> None:
    asked: list[bool] = []

    def teams() -> list[str]:
        asked.append(True)
        return ["a", "b"]

    complete = asking.choices(teams)
    assert asked == []
    assert complete("") == ["a", "b"] and complete("b") == ["b"] and asked == [True]
