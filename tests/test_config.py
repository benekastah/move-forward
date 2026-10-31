from pathlib import Path

import pytest
from conftest import NOW
from pydantic import ValidationError

from move_forward import config
from move_forward.db import Database
from move_forward.plugins.github.config import DEFAULT_PLACEHOLDER_LABELS
from move_forward.plugins.jira.config import DEFAULT_JIRA_TOKEN_ENV
from move_forward.plugins.slack.config import DEFAULT_PING_FIRST, DEFAULT_PING_FOLLOW_UP
from move_forward.registry import enabled

MINIMAL = 'email = "me@acme.com"\n'


def _load(tmp_path: Path, text: str) -> config.Config:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return config.load(path)


def test_load_expands_paths_and_defaults(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        'email = "me@acme.com"\nteams = ["core", "web"]\nnotes_dir = "~/n"\n\n'
        '[github]\norg = "acme"\n\n[github.teams]\nweb = "web-reviewers"\n\n'
        '[git]\nrepos = ["~/src/a"]\n\n[slack.teams]\ncore = "#team-core"\n\n'
        '[jira]\nsite = "https://j"\n',
    )
    assert loaded.git is not None and loaded.git.repos == [Path("~/src/a").expanduser()]
    assert loaded.notes_dir == Path("~/n").expanduser()
    assert loaded.github is not None and loaded.github.review_checks == []
    assert [loaded.github.slug(t) for t in loaded.teams] == ["core", "web-reviewers"]
    assert loaded.slack is not None and loaded.slack.teams == {"core": "#team-core"}
    assert loaded.slack.ping_first == DEFAULT_PING_FIRST
    assert loaded.port == config.DEFAULT_PORT
    assert loaded.jira is not None and loaded.jira.token_env == DEFAULT_JIRA_TOKEN_ENV


def test_a_config_with_no_sections_enables_no_plugins(tmp_path: Path) -> None:
    loaded = _load(tmp_path, MINIMAL)
    assert (loaded.github, loaded.git, loaded.jira, loaded.slack) == (None, None, None, None)
    assert loaded.teams == []
    assert loaded.notes_dir == Path("~/.config/move-forward/notes").expanduser()


def test_unknown_keys_are_errors(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="prot"):
        _load(tmp_path, MINIMAL + "prot = 1\n")
    with pytest.raises(ValidationError, match="prot"):
        _load(tmp_path, MINIMAL + "\n[git]\nrepos = []\nprot = 1\n")


def test_moved_settings_say_where_they_went(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"`repos` is now in \[git\]"):
        _load(tmp_path, MINIMAL + "repos = []\n")


def test_a_plugin_team_must_be_a_top_level_team(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"\[slack.teams\] has wbe"):
        _load(tmp_path, MINIMAL + 'teams = ["web"]\n\n[slack.teams]\nwbe = "#web"\n')


def test_naming_settings_have_defaults_and_load(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        'email = "me@acme.com"\nprune_days = 30\n\n[github]\norg = "acme"\n\n'
        '[slack]\nping_first = "Look: {url}"\n\n'
        '[jira]\nsite = "https://j"\nstatus_closed = "Done"\nincident_types = ["sev"]\n',
    )
    assert loaded.slack is not None and loaded.prune_days == 30
    assert loaded.slack.ping_first == "Look: {url}"
    assert loaded.slack.ping_follow_up == DEFAULT_PING_FOLLOW_UP
    assert loaded.github is not None
    assert loaded.github.placeholder_labels == DEFAULT_PLACEHOLDER_LABELS
    assert loaded.jira is not None
    assert loaded.jira.status_closed == "Done" and loaded.jira.status_in_review == "In Review"
    assert loaded.jira.incident_types == ["sev"] and loaded.jira.bug_types == ["bug"]


def test_only_plugins_with_a_section_are_enabled(tmp_path: Path) -> None:
    loaded = _load(tmp_path, MINIMAL + "\n[git]\nrepos = []\n")
    assert [e.name for e in enabled(loaded, Database(":memory:"), NOW)] == ["git"]
