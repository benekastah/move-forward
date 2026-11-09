import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from conftest import section
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from move_forward import asking, cli, config, init, keychain, registry
from move_forward.plugin import Plugin
from move_forward.plugins.git import setup as git_setup
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git_stack.plugin import GitStackPlugin
from move_forward.plugins.github import setup as github_setup
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.slack.config import SlackConfig
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

    def guess_teams(org: str, host: str = "github.com") -> list[str]:
        return ["core"] if org == "acme" else []

    def org_team_examples(org: str, host: str = "github.com") -> list[str]:
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
    assert loaded.section("github") is not None and loaded.section("git") is not None
    assert section(loaded, "github", GitHubConfig).org == "acme" and loaded.teams == ["core"]
    assert loaded.section("slack") is None
    assert loaded.email == "jane@acme.com"
    assert section(loaded, "git", GitConfig).repos == [tmp_path / "app"]
    assert (
        section(loaded, "git", GitConfig).my_branch_prefixes == []
        and loaded.port == config.DEFAULT_PORT
    )
    assert loaded.section("jira") is None and loaded.section("git_stack") is None
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
    assert config.load(path).section("git_stack") is not None


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
    assert loaded.section("github") is not None and loaded.section("git") is not None
    assert section(loaded, "github", GitHubConfig).org == "other" and loaded.teams == ["a", "b"]
    assert loaded.section("slack") is not None and section(loaded, "slack", SlackConfig).teams == {
        "a": "#team-a"
    }
    assert loaded.email == "jane@other.com"
    # Each token chosen is named, or its plugin says what to do instead.
    assert "For Jira: create an API token at https://id.atlassian.com/" in message
    assert message.endswith(
        "For Slack: `mfd token set slack` (or export SLACK_API_TOKEN) stores its token"
    )
    assert section(loaded, "git", GitConfig).repos == [tmp_path / "x", tmp_path / "y"]
    assert section(loaded, "slack", SlackConfig).workspace_url == "https://o.slack.com"
    assert section(loaded, "git", GitConfig).my_branch_prefixes == ["jane/"] and loaded.port == 9000
    assert (
        loaded.section("jira") is not None
        and section(loaded, "jira", JiraConfig).site == "https://o.atlassian.net"
    )
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
    assert (
        loaded.section("github") is not None
        and section(loaded, "github", GitHubConfig).org == "acme"
        and loaded.port == 9000
    )
    assert (
        loaded.section("slack") is not None
        and section(loaded, "slack", SlackConfig).workspace_url == "https://acme.slack.com"
    )
    assert (
        loaded.section("jira") is not None
        and section(loaded, "jira", JiraConfig).site == "https://acme.atlassian.net"
    )


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
    git = config.load(path).section("git", GitConfig)
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
        "ticket_link_prefixes",
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
    assert loaded.section("github") is None and loaded.teams == ["web", "api"]
    assert loaded.section("slack") is not None and section(loaded, "slack", SlackConfig).teams == {
        "web": "#web"
    }
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
    assert github_setup.org("https://GitHub.Acme.com/acme") == "github.acme.com/acme"
    path = tmp_path / "config.toml"
    init.init(path, scripted({"organization": "https://github.com/other"}), cwd=tmp_path)
    github = config.load(path).section("github", GitHubConfig)
    assert github is not None and (github.host, github.org) == ("github.com", "other")
    enterprise = tmp_path / "enterprise.toml"
    init.init(enterprise, scripted({"organization": "https://github.acme.com/acme"}), cwd=tmp_path)
    github = config.load(enterprise).section("github", GitHubConfig)
    assert github is not None and (github.host, github.org) == ("github.acme.com", "acme")


def test_tab_completes_plugins_the_orgs_teams_and_directories(
    tmp_path: Path, no_machine_guesses: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched: list[str] = []

    def org_teams(org: str, host: str = "github.com") -> list[str]:
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
    assert completions["Plugins"] == [
        "git",
        "git_stack",
        "github",
        "jira",
        "mail",
        "slack",
        "vcard",
    ]
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


def test_notifications_are_off_unless_chosen(tmp_path: Path, no_machine_guesses: None) -> None:
    off = tmp_path / "off.toml"
    init.init(off, scripted({"Plugins": "git", "Teams": ""}), cwd=tmp_path)
    assert config.load(off).notifications.subscribe == []
    assert "# [notifications]" in off.read_text()
    on = tmp_path / "on.toml"
    answers: dict[str, str | list[str]] = {
        "Plugins": "git",
        "Teams": "",
        "Notifications": "dashboard.source_failing",
    }
    init.init(on, scripted(answers), cwd=tmp_path)
    assert config.load(on).notifications.subscribe == ["dashboard.source_failing"]


def test_an_unknown_notification_is_asked_again(tmp_path: Path, no_machine_guesses: None) -> None:
    path = tmp_path / "config.toml"
    answers: dict[str, str | list[str]] = {
        "Plugins": "git",
        "Teams": "",
        "Notifications": ["nope", "dashboard.source_failing"],
    }
    init.init(path, scripted(answers), cwd=tmp_path)
    assert config.load(path).notifications.subscribe == ["dashboard.source_failing"]


def _tables(field: FieldInfo) -> bool:
    """A field whose entries are tables of their own, by name (mail's `[mail.<name>]`)."""
    return field.json_schema_extra == {"tables": True}


@pytest.mark.parametrize("plugin", registry.PLUGINS, ids=lambda plugin: plugin.name)
def test_every_optional_key_of_a_plugin_is_a_commented_default(plugin: Plugin[Any, Any]) -> None:
    written = plugin.init_template + plugin.init_example
    missing = [
        key
        for key, field in plugin.config_model.model_fields.items()
        if not field.is_required()
        and key != "teams"
        and f"# {key} =" not in written
        and f"[{plugin.name}.{key}]" not in written  # a table of its own
        and not (_tables(field) and f"# [{plugin.name}." in written)  # tables by name
    ]
    assert missing == []


@pytest.mark.parametrize("plugin", registry.PLUGINS, ids=lambda plugin: plugin.name)
def test_every_key_of_a_plugin_is_in_the_example(plugin: Plugin[Any, Any]) -> None:
    """`mfd init --example` shows each plugin's keys too, in its section."""
    section = _example_section(plugin)
    keys = [key for key in plugin.config_model.model_fields if key != "teams"]
    if section is None:
        assert keys == [], f"the example has no [{plugin.name}]"
        return
    shown = "\n".join(section)
    missing = [
        key
        for key in keys
        if f"{key} =" not in shown
        and f"[{plugin.name}.{key}]" not in shown
        and not (_tables(plugin.config_model.model_fields[key]) and f"[{plugin.name}." in shown)
    ]
    assert missing == []


def test_every_key_of_core_is_in_the_example() -> None:
    """Core's top-level keys, before any section, and those of its own sections (`[act_now]`
    and the like), each as a whole key starting a commented line."""
    example = cli.EXAMPLE_CONFIG
    top = re.split(r"^(?:# )?\[", example, maxsplit=1, flags=re.MULTILINE)[0]

    def has(key: str, text: str) -> bool:
        return re.search(rf"^(?:#+ ?)?{re.escape(key)} =", text, re.MULTILINE) is not None

    missing: list[str] = []
    for name, field in config.Config.model_fields.items():
        if name == "sections":  # the plugins' own, checked by plugin below
            continue
        model = field.annotation
        if isinstance(model, type) and issubclass(model, BaseModel):
            section = re.search(rf"^# \[{name}\][^\n]*\n((?:#[^\n]*\n)*)", example, re.MULTILINE)
            shown = section[1] if section else ""
            missing += [f"{name}.{key}" for key in model.model_fields if not has(key, shown)]
        elif not has(name, top):
            missing.append(name)
    assert missing == []


def _example_section(plugin: Plugin[Any, Any]) -> list[str] | None:
    """`mfd init --example`'s lines for a plugin's section, up to the next section's; None
    when it has none."""
    lines = [str(line) for line in cli.EXAMPLE_CONFIG.splitlines()]
    header = re.compile(r"^(# )?\[([a-z_]+)\]")
    start = next(
        (n for n, line in enumerate(lines) if (m := header.match(line)) and m[2] == plugin.name),
        None,
    )
    if start is None:
        return None
    end = next((n for n in range(start + 1, len(lines)) if header.match(lines[n])), len(lines))
    return lines[start:end]


_KEY = re.compile(r"^([a-z_]+) =")


@pytest.mark.parametrize("plugin", registry.PLUGINS, ids=lambda plugin: plugin.name)
def test_a_plugins_example_uncommented_is_a_config_it_takes(plugin: Plugin[Any, Any]) -> None:
    """Uncommenting `mfd init --example`'s section for a plugin gives a section it accepts, and
    so does taking what's behind `##` instead (several mailboxes, say): its tables, each a
    mailbox of its own, and what every one of them sets moved out of the section."""
    section = _example_section(plugin)
    if section is None:
        return  # no section to give (notes: always on)
    _check_uncommented(plugin, section)
    if "\n##" in f"\n{plugin.init_template}":
        # And `mfd init`'s copy of the alternative, with its required keys as in the example.
        written = f"[{plugin.name}]\n{plugin.init_example}{plugin.init_template}"
        _check_uncommented(plugin, written.splitlines())


def _check_uncommented(plugin: Plugin[Any, Any], section: list[str]) -> None:
    """`section`, a plugin's commented config, uncommented, is a config the plugin takes, and
    so is what's behind `##` instead (see the test above)."""
    base = [line.removeprefix("# ") for line in section if not line.startswith("##")]
    plugin.config_model.model_validate(tomllib.loads("\n".join(base))[plugin.name])
    instead = [line.removeprefix("## ") for line in section if line.startswith("## ")]
    if not instead:
        return
    tables: list[set[str]] = []
    for line in instead:
        if line.startswith("["):
            tables.append(set())
        elif tables and (m := _KEY.match(line)):
            tables[-1].add(m[1])
    moved: set[str] = tables[0].intersection(*tables[1:]) if tables else set()
    kept = [line for line in base if not ((m := _KEY.match(line)) and m[1] in moved)]
    text = "\n".join(
        [*kept, *(line for line in instead if line.startswith("[") or _KEY.match(line))]
    )
    plugin.config_model.model_validate(tomllib.loads(text)[plugin.name])


def test_a_plugins_token_help_is_its_next_step(
    tmp_path: Path, no_machine_guesses: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(JiraPlugin, "token_help", "make one at https://id.example/tokens")
    message = init.init(
        tmp_path / "config.toml",
        scripted({"Plugins": "jira", "Jira site": "https://o.atlassian.net"}),
        cwd=tmp_path,
    )
    assert message.endswith("For Jira: make one at https://id.example/tokens")


def test_a_plugin_with_several_tokens_names_them_on_one_line(
    tmp_path: Path, no_machine_guesses: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def tokens(self: JiraPlugin, config: JiraConfig) -> dict[str, keychain.TokenSettings]:
        return {"home": _Token("A_TOKEN", "a"), "work": _Token("B_TOKEN", "b")}

    monkeypatch.setattr(JiraPlugin, "tokens", tokens)
    monkeypatch.setattr(JiraPlugin, "token_help", "")  # the plain line, naming each
    message = init.init(
        tmp_path / "config.toml",
        scripted({"Plugins": "jira", "Jira site": "https://o.atlassian.net"}),
        cwd=tmp_path,
    )
    assert message.endswith(
        "For Jira: `mfd token set jira.home` (or export A_TOKEN) and `mfd token set jira.work`"
        " (or export B_TOKEN) store its tokens"
    )


class _Token:
    def __init__(self, env: str, service: str) -> None:
        self.token_env, self.keychain_service = env, service
