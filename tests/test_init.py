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


LEFT_OUT = init.render_config(init.EXAMPLE_ANSWERS, {})
"""What `mfd init` writes when asked for no plugin: every offered plugin's section commented
out."""
_COMMENTED_KEY = re.compile(r"^# ([a-z_]+) = (.*?)(?:  # (.*))?$")
"""A commented-out key as `mfd init` writes it: its name, value and comment."""


@pytest.mark.parametrize("plugin", init.offered(), ids=lambda plugin: plugin.name)
def test_every_key_of_a_plugin_left_out_is_a_commented_line(plugin: Plugin[Any, Any]) -> None:
    """A plugin's required keys (with its example values), its optional keys (with their
    defaults), and its tables (with an example), each commented out."""
    shown = "\n".join(_section_lines(LEFT_OUT, plugin.name) or [])
    missing = [
        key
        for key, field in plugin.config_model.model_fields.items()
        if f"\n# {key} =" not in f"\n{shown}"
        and f"# [{plugin.name}.{key}]" not in shown  # a table of its own
        and not (_tables(field) and f"# [{plugin.name}." in shown)  # tables by name
    ]
    assert missing == []


@pytest.mark.parametrize("plugin", init.offered(), ids=lambda plugin: plugin.name)
def test_what_mfd_init_writes_for_a_plugin_loads_uncommented(plugin: Plugin[Any, Any]) -> None:
    """Its section left out, uncommented, and its section chosen (with its example's
    values), its commented defaults uncommented, are each a section it takes."""
    left_out = _section_lines(LEFT_OUT, plugin.name)
    assert left_out is not None
    _check_uncommented(plugin, left_out)
    values = tomllib.loads(_uncomment(plugin.init_example))
    text = init.render_config(init.EXAMPLE_ANSWERS, {plugin.name: values})
    chosen = _section_lines(text, plugin.name)
    assert chosen is not None and not chosen[0].startswith("#")
    _check_uncommented(plugin, chosen)


@pytest.mark.parametrize("plugin", init.offered(), ids=lambda plugin: plugin.name)
def test_a_plugins_commented_defaults_are_its_defaults(plugin: Plugin[Any, Any]) -> None:
    """Uncommenting a left-out section's optional keys changes nothing, but for the keys it
    shows an example of instead (`init_shown`) and its example tables (`init_tables`)."""
    lines = _section_lines(LEFT_OUT, plugin.name) or []
    uncommented = _uncomment("\n".join(line for line in lines if not line.startswith("##")))
    loaded = plugin.config_model.model_validate(tomllib.loads(uncommented)[plugin.name])
    bare = plugin.config_model.model_validate(tomllib.loads(_uncomment(plugin.init_example)))
    fields = plugin.config_model.model_fields
    examples = {
        *plugin.init_shown,
        *(k for k in fields if f"[{plugin.name}.{k}]" in plugin.init_tables),
    }
    assert loaded.model_dump(exclude=examples) == bare.model_dump(exclude=examples)


def test_cores_commented_defaults_are_its_defaults() -> None:
    """Uncommenting core's top-level keys and its own tables changes nothing."""
    top = LEFT_OUT.split("\n# [", 1)[0]  # up to the first section, a plugin's
    loaded = config.Config.model_validate(
        tomllib.loads(_uncomment(top) + "\n" + _uncomment(init.core_tables()))
    )
    assert loaded == config.Config.model_validate(init.EXAMPLE_ANSWERS)
    assert "# prune_days = 90" in top and "# [act_now]" in init.core_tables()


def _uncomment(text: str) -> str:
    """`text` with its commented-out keys and tables uncommented, but not its prose."""
    return "\n".join(
        line[2:] if re.match(r"# (\[|[a-z_-]+ =)", line) else line for line in text.splitlines()
    )


def test_a_keys_comment_is_the_first_sentence_of_its_docstring() -> None:
    assert init.summary("Worktrees to leave out, e.g. ones agents\n    create. More.") == (
        "Worktrees to leave out, e.g. ones agents create."
    )
    assert init.summary("`[act_now]`: how core ranks. More.") == "How core ranks."
    assert init.summary("No sentence") == "No sentence"


def test_every_commented_key_has_its_docstrings_first_sentence() -> None:
    """Each optional key `mfd init` writes, core's and the plugins', has a docstring, whose
    first sentence is its comment."""
    models: dict[str, type[BaseModel]] = {"": config.Config}
    models |= {
        key: field.annotation
        for key, field in config.Config.model_fields.items()
        if isinstance(field.annotation, type)
        and issubclass(field.annotation, BaseModel)
        and key not in init.HAND_WRITTEN
    }
    models |= {plugin.name: plugin.config_model for plugin in init.offered()}
    examples = {p.name: tomllib.loads(_uncomment(p.init_example)) for p in init.offered()}
    table = ""
    checked: list[str] = []
    for line in LEFT_OUT.splitlines():
        if header := re.match(r"^# \[([a-z_]+)\]", line):
            table = header[1]
        elif (key := _COMMENTED_KEY.match(line)) and table in models:
            field = models[table].model_fields.get(key[1])
            if field is None or key[1] in examples.get(table, {}):
                continue  # an example table's key (`[github.teams]`'s), or the init_example's
            assert field.description, f"{table}.{key[1]} has no docstring"
            assert key[3] == init.summary(field.description), f"{table}.{key[1]}"
            checked.append(f"{table}.{key[1]}")
    assert {"prune_days", "act_now.size", "github.history_days", "mail.port"} <= {
        name.removeprefix(".") for name in checked
    }


def test_a_tokens_place_shows_the_sections_own_default() -> None:
    lines = _section_lines(LEFT_OUT, "jira") or []
    assert any(line.startswith('# token_env = "JIRA_API_TOKEN"') for line in lines)
    assert any(line.startswith('# keychain_service = "move-forward-jira"') for line in lines)


def test_answered_keys_are_not_commented_defaults_too() -> None:
    text = init.render_config(
        {"email": "e@x.y", "teams": [], "port": 9000}, {"github": {"org": "a", "host": "h"}}
    )
    top = text.split("\n# [", 1)[0]
    github = "\n".join(_section_lines(text, "github") or [])
    assert "# port =" not in top and "\nport = 9000" in top
    assert "# host =" not in github and 'host = "h"' in github


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
    example = init.example()
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


def test_mfd_init_example_writes_a_config_with_the_plugins_their_examples_turn_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    assert cli.main(["-c", str(path), "init", "--example"]) == 0
    assert "wrote" in capsys.readouterr().out
    loaded = config.load(path)
    on = [name for name in registry.enabled_names(loaded) if name != "notes"]  # always on
    assert on == ["git", "git_stack", "github", "slack"]  # jira and mail start commented out
    assert section(loaded, "github", GitHubConfig).org == "example-org"


def _example_section(plugin: Plugin[Any, Any]) -> list[str] | None:
    """`mfd init --example`'s lines for a plugin's section; None when it has none."""
    return _section_lines(init.example(), plugin.name)


def _section_lines(text: str, name: str) -> list[str] | None:
    """The lines of `text`, a config `mfd init` wrote, for the section `name` (commented out or
    not), with its own tables, up to the next section's; None when it has none."""
    lines = text.splitlines()
    header = re.compile(r"^(# )?\[([a-z_]+)\]")
    start = next(
        (n for n, line in enumerate(lines) if (m := header.match(line)) and m[2] == name),
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
    mailbox of its own, and what any of them sets moved out of the section."""
    section = _example_section(plugin)
    if section is None:
        return  # no section to give (notes: always on)
    _check_uncommented(plugin, section)


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
    moved: set[str] = set[str]().union(*tables)
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
