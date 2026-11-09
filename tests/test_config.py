from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, section
from pydantic import ValidationError

from move_forward import config, registry
from move_forward.db import Database, Migration
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.github.config import DEFAULT_PLACEHOLDER_LABELS, GitHubConfig
from move_forward.plugins.jira.config import DEFAULT_JIRA_TOKEN_ENV, JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.slack.config import (
    DEFAULT_PING_FIRST,
    DEFAULT_PING_FOLLOW_UP,
    SlackConfig,
)
from move_forward.registry import enabled

MINIMAL = 'email = "me@acme.com"\n'


def test_github_reviewer_assignment_defaults_and_team_overrides() -> None:
    default = GitHubConfig(org="acme")
    assert default.default_team_reviewer_assignment == "team"
    assert default.team_reviewer_assignment == {}
    configured = GitHubConfig.model_validate(
        {
            "org": "acme",
            "default_team_reviewer_assignment": "team",
            "team_reviewer_assignment": {"bacon": "individual"},
        }
    )
    assert configured.team_reviewer_assignment == {"bacon": "individual"}
    with pytest.raises(ValidationError):
        GitHubConfig.model_validate({"org": "acme", "team_reviewer_assignment": {"bacon": "any"}})


def _load(tmp_path: Path, text: str) -> config.Config:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return config.load(path)


def test_load_expands_paths_and_defaults(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        'email = "me@acme.com"\nteams = ["core", "web"]\n\n'
        '[github]\norg = "acme"\n\n[github.teams]\nweb = "web-reviewers"\n\n'
        '[git]\nrepos = ["~/src/a"]\n\n[slack.teams]\ncore = "#team-core"\n\n'
        '[jira]\nsite = "https://j"\n',
    )
    assert loaded.section("git") is not None and section(loaded, "git", GitConfig).repos == [
        Path("~/src/a").expanduser()
    ]
    assert (
        loaded.section("github") is not None
        and section(loaded, "github", GitHubConfig).policy_checks == []
    )
    assert [section(loaded, "github", GitHubConfig).slug(t) for t in loaded.teams] == [
        "core",
        "web-reviewers",
    ]
    assert loaded.section("slack") is not None and section(loaded, "slack", SlackConfig).teams == {
        "core": "#team-core"
    }
    assert section(loaded, "slack", SlackConfig).ping_first == DEFAULT_PING_FIRST
    assert loaded.port == config.DEFAULT_PORT
    assert (
        loaded.section("jira") is not None
        and section(loaded, "jira", JiraConfig).token_env == DEFAULT_JIRA_TOKEN_ENV
    )


def test_a_config_with_no_sections_enables_no_plugins(tmp_path: Path) -> None:
    loaded = _load(tmp_path, MINIMAL)
    assert (
        loaded.section("github"),
        loaded.section("git"),
        loaded.section("jira"),
        loaded.section("slack"),
    ) == (None, None, None, None)
    assert loaded.teams == []


def test_unknown_keys_are_errors(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="prot"):
        _load(tmp_path, MINIMAL + "prot = 1\n")
    with pytest.raises(ValidationError, match="prot"):
        _load(tmp_path, MINIMAL + "\n[git]\nrepos = []\nprot = 1\n")


def test_moved_settings_say_where_they_went(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"`repos` is now in \[git\]"):
        _load(tmp_path, MINIMAL + "repos = []\n")
    with pytest.raises(
        ValidationError,
        match=r"`reviewer_history_days` in \[github\] is now `commit_history_days` in \[git\]",
    ):
        _load(tmp_path, MINIMAL + '\n[github]\norg = "acme"\nreviewer_history_days = 90\n')
    with pytest.raises(ValidationError, match=r"`notes_dir` is gone; .*mfd import"):
        _load(tmp_path, MINIMAL + 'notes_dir = "~/n"\n')
    with pytest.raises(ValidationError, match=r"`https` is gone; .*plain HTTP.*delete the line"):
        _load(tmp_path, MINIMAL + "https = true\n")
    with pytest.raises(ValidationError, match=r"`refresh_on_load_seconds` is gone; .*delete the"):
        _load(tmp_path, MINIMAL + "refresh_on_load_seconds = 60\n")
    with pytest.raises(ValidationError, match=r"`dir` is gone; .*mfd import"):
        _load(tmp_path, MINIMAL + '\n[notes]\ndir = "~/n"\n')


def test_a_removed_plugins_section_says_it_was_removed(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"\[vcard\] plugin was removed: delete the section"):
        _load(tmp_path, MINIMAL + '\n[vcard]\npaths = ["~/.contacts"]\n')


def test_a_jira_teams_table_says_its_gone(tmp_path: Path) -> None:
    text = MINIMAL + 'teams = ["core"]\n\n[git]\nrepos = []\n\n[jira]\nsite = "https://j"\n'
    with pytest.raises(ValidationError, match=r"`\[jira.teams\]` is gone; .*delete the table"):
        _load(tmp_path, text + '\n[jira.teams]\ncore = "12345"\n')


def test_a_subscription_to_the_certificates_notification_says_its_gone(tmp_path: Path) -> None:
    text = MINIMAL + '\n[notifications]\nsubscribe = ["dashboard.certificate_ending"]\n'
    with pytest.raises(ValidationError, match="gone with HTTPS mode: take it out"):
        _load(tmp_path, text)


@pytest.mark.parametrize("kind", ["jira.comment_added", "jira.mentioned"])
def test_a_subscription_to_a_jira_comment_notification_says_its_gone(
    tmp_path: Path, kind: str
) -> None:
    text = MINIMAL + f'\n[notifications]\nsubscribe = ["{kind}"]\n'
    with pytest.raises(ValidationError, match=rf"{kind}, which is gone .*Slack.*: take it out"):
        _load(tmp_path, text)


def test_a_snooze_days_key_with_a_kinds_old_name_says_its_new_one(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="the kind 'pr' is called 'pull_request' now"):
        _load(tmp_path, MINIMAL + "\n[act_now]\nsnooze_days = { pr = 7 }\n")
    with pytest.raises(ValidationError, match="the kind 'entry' is called 'notes.entry' now"):
        _load(tmp_path, MINIMAL + "\n[act_now]\nsnooze_days = { entry = 2 }\n")
    config = _load(
        tmp_path, MINIMAL + '\n[act_now]\nsnooze_days = { pull_request = 7, "notes.entry" = 2 }\n'
    )
    assert config.act_now.snooze_days == {"pull_request": 7, "notes.entry": 2}


def test_a_plugin_team_must_be_a_top_level_team(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"\[slack.teams\] has wbe"):
        _load(tmp_path, MINIMAL + 'teams = ["web"]\n\n[slack.teams]\nwbe = "#web"\n')


def test_naming_settings_have_defaults_and_load(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        'email = "me@acme.com"\nprune_days = 30\n\n[github]\norg = "acme"\n\n'
        '[slack]\nping_first = "Look: {url}"\n\n'
        '[jira]\nsite = "https://j"\nstatus_closed = ["Done"]\nincident_types = ["sev"]\n',
    )
    assert loaded.section("slack") is not None and loaded.prune_days == 30
    assert section(loaded, "slack", SlackConfig).ping_first == "Look: {url}"
    assert section(loaded, "slack", SlackConfig).ping_follow_up == DEFAULT_PING_FOLLOW_UP
    assert loaded.section("github") is not None
    assert section(loaded, "github", GitHubConfig).placeholder_labels == DEFAULT_PLACEHOLDER_LABELS
    assert loaded.section("jira") is not None
    jira = section(loaded, "jira", JiraConfig)
    assert jira.status_closed == ["Done"] and jira.status_in_review == ["In Review"]
    assert jira.incident_types == ["sev"] and jira.bug_types == ["bug"]


def test_only_plugins_with_a_section_are_enabled(tmp_path: Path) -> None:
    loaded = _load(tmp_path, MINIMAL + "\n[git]\nrepos = []\n")
    assert [e.name for e in enabled(loaded, Database(":memory:"), NOW)] == ["git", "notes"]


class _JiraOnGit(JiraPlugin):
    requires: ClassVar[list[str]] = ["git"]


def test_a_plugin_needs_the_plugins_it_requires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = [_JiraOnGit() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    jira = '\n[jira]\nsite = "https://j"\n'
    with pytest.raises(ValidationError, match=r"\[jira\] needs \[git\]"):
        _load(tmp_path, MINIMAL + jira)
    loaded = _load(tmp_path, MINIMAL + jira + "\n[git]\nrepos = []\n")
    assert loaded.section("jira") is not None


def test_the_jira_site_must_be_https(tmp_path: Path) -> None:
    for site in ("http://j.atlassian.net", "j.atlassian.net"):
        with pytest.raises(ValidationError, match="must be an https:// URL"):
            _load(tmp_path, MINIMAL + f'\n[jira]\nsite = "{site}"\n')


class _JiraWithTable(JiraPlugin):
    migrations: ClassVar[list[Migration]] = [
        Migration("0001_seen", "CREATE TABLE jira_seen (key TEXT PRIMARY KEY);")
    ]


def test_plugin_migrations_run_when_migrated_not_on_every_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = [_JiraWithTable() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    loaded = _load(tmp_path, MINIMAL + '\n[jira]\nsite = "https://j"\n')
    db = Database(":memory:")
    calls: list[str] = []
    real_migrate = db.migrate

    def counting(owner: str, migrations: list[Migration], owners: Iterable[str] = ()) -> int:
        calls.append(owner)
        return real_migrate(owner, migrations, owners)

    monkeypatch.setattr(db, "migrate", counting)
    enabled(loaded, db, NOW)
    assert calls == []
    registry.migrate(loaded, db)
    assert "jira" in calls
    assert db.query("SELECT * FROM jira_seen") == []


def test_act_now_ranking_is_tuned_in_its_section(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('email = "me@x"\n[act_now]\nsize = 3\nweights = { waiting = 5 }\n')
    loaded = config.load(path)
    assert (loaded.act_now.size, loaded.act_now.per_plugin) == (3, 3)
    assert (loaded.act_now.weights.waiting, loaded.act_now.weights.closes) == (5, 3)
    path.write_text('email = "me@x"\n[act_now]\nweights = { speed = 1 }\n')
    with pytest.raises(ValidationError, match="speed"):
        config.load(path)


def test_refresh_seconds_moved_into_each_plugins_section(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"`refresh_seconds` is now per plugin"):
        _load(tmp_path, MINIMAL + "refresh_seconds = 60\n")


def test_each_plugin_section_is_its_plugins_model(tmp_path: Path) -> None:
    loaded = _load(tmp_path, MINIMAL + '\n[git]\nrepos = []\n\n[github]\norg = "acme"\n')
    for name in ("git", "github", "notes"):  # notes is always on
        plugin = next(p for p in registry.PLUGINS if p.name == name)
        assert isinstance(loaded.section(name), plugin.config_model)
    assert loaded.section("jira") is None


def test_an_unknown_section_names_the_plugins(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"no plugin is named \[linear\]; the plugins are"):
        _load(tmp_path, MINIMAL + "\n[linear]\nteam = 'x'\n")


def test_a_bad_section_says_which_of_its_fields(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"github\.orgg\n  Extra inputs"):
        _load(tmp_path, MINIMAL + '\n[github]\norg = "acme"\norgg = "x"\n')


def test_sections_isnt_a_setting(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="`sections` isn't a setting"):
        _load(tmp_path, MINIMAL + '\n[sections.github]\norg = "acme"\n')
