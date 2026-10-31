from pathlib import Path

import pytest
from pydantic import ValidationError

from move_forward_dashboard import config


def test_load_expands_paths_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'org = "acme"\nrepos = ["~/src/a"]\nnotes_dir = "~/n"\n\n[[teams]]\ngithub_slug = "core"\n'
        'slack_channel = "#team-core"\n\n[jira]\nsite = "https://j"\nemail = "me@x"\n'
    )
    loaded = config.load(path)
    assert loaded.repos == [Path("~/src/a").expanduser()]
    assert loaded.team_slugs == ["core"] and loaded.teams[0].slack_channel == "#team-core"
    assert loaded.teams[0].jira_team_id is None
    assert loaded.review_checks == [] and loaded.required_owner_marker == ""
    assert loaded.port == config.DEFAULT_PORT
    assert loaded.notes_dir == Path("~/n").expanduser()
    assert loaded.jira is not None and loaded.jira.escalation_label_prefix == ""
    assert loaded.jira is not None and loaded.jira.token_env == config.DEFAULT_JIRA_TOKEN_ENV


def test_unknown_keys_are_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('org = "acme"\nteams = []\nrepos = []\nnotes_dir = "n"\nprot = 1\n')
    with pytest.raises(ValidationError, match="prot"):
        config.load(path)


def test_naming_settings_have_defaults_and_load(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'org = "acme"\nteams = []\nrepos = []\nnotes_dir = "n"\nping_first = "Look: {url}"\n'
        "prune_days = 30\n\n"
        '[jira]\nsite = "https://j"\nemail = "me@x"\nstatus_closed = "Done"\nincident_types = ["sev"]\n'
    )
    loaded = config.load(path)
    assert loaded.ping_first == "Look: {url}" and loaded.prune_days == 30
    assert loaded.ping_follow_up == config.DEFAULT_PING_FOLLOW_UP
    assert loaded.placeholder_labels == config.DEFAULT_PLACEHOLDER_LABELS
    assert loaded.jira is not None
    assert loaded.jira.status_closed == "Done" and loaded.jira.status_in_review == "In Review"
    assert loaded.jira.incident_types == ["sev"] and loaded.jira.bug_types == ["bug"]
