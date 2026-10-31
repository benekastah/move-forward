from pathlib import Path

import pytest
from pydantic import ValidationError

from move_forward_dashboard import config

MINIMAL = 'email = "me@acme.com"\nrepos = []\n\n[github]\norg = "acme"\n'


def test_load_expands_paths_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'email = "me@acme.com"\nrepos = ["~/src/a"]\nnotes_dir = "~/n"\n\n'
        '[github]\norg = "acme"\n\n[teams.core]\nslack_channel = "#team-core"\n\n'
        '[teams.ci-reviewers]\n\n[jira]\nsite = "https://j"\n'
    )
    loaded = config.load(path)
    assert loaded.repos == [Path("~/src/a").expanduser()]
    assert loaded.notes_dir == Path("~/n").expanduser()
    assert loaded.reviewer_email_domains == ["acme.com"]
    assert loaded.github.org == "acme" and loaded.github.review_checks == []
    assert loaded.team_slugs == ["core", "ci-reviewers"]
    assert loaded.teams["core"].slack_channel == "#team-core"
    assert loaded.teams["ci-reviewers"].slack_channel is None
    assert loaded.slack.workspace_url == "" and loaded.slack.ping_first == config.DEFAULT_PING_FIRST
    assert loaded.port == config.DEFAULT_PORT
    assert loaded.jira is not None and loaded.jira.token_env == config.DEFAULT_JIRA_TOKEN_ENV
    assert loaded.jira.escalation_label_prefix == ""


def test_minimal_config_has_defaults_and_no_teams_slack_or_jira(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL)
    loaded = config.load(path)
    assert loaded.teams == {} and loaded.jira is None and loaded.slack.workspace_url == ""
    assert loaded.notes_dir == config.DEFAULT_NOTES_DIR.expanduser()
    assert loaded.notes_dir == Path("~/.config/move-forward/notes").expanduser()
    path.write_text(MINIMAL.replace("repos", 'email_domains = ["a.com", "b.io"]\nrepos'))
    assert config.load(path).reviewer_email_domains == ["a.com", "b.io"]


def test_unknown_keys_are_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(MINIMAL + "prot = 1\n")
    with pytest.raises(ValidationError, match="prot"):
        config.load(path)
    path.write_text(MINIMAL + "prot = 1\n\n[slack]\n")
    with pytest.raises(ValidationError, match="prot"):
        config.load(path)


def test_naming_settings_have_defaults_and_load(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'email = "me@acme.com"\nrepos = []\nprune_days = 30\n\n[github]\norg = "acme"\n\n'
        '[slack]\nping_first = "Look: {url}"\n\n'
        '[jira]\nsite = "https://j"\nstatus_closed = "Done"\nincident_types = ["sev"]\n'
    )
    loaded = config.load(path)
    assert loaded.slack.ping_first == "Look: {url}" and loaded.prune_days == 30
    assert loaded.slack.ping_follow_up == config.DEFAULT_PING_FOLLOW_UP
    assert loaded.github.placeholder_labels == config.DEFAULT_PLACEHOLDER_LABELS
    assert loaded.jira is not None
    assert loaded.jira.status_closed == "Done" and loaded.jira.status_in_review == "In Review"
    assert loaded.jira.incident_types == ["sev"] and loaded.jira.bug_types == ["bug"]
