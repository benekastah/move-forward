"""Explicit channel discovery is batched, read-only, and reports ambiguous matches."""

import json
from pathlib import Path
from typing import Any

import pytest

from move_forward import cli
from move_forward.db import Database
from move_forward.plugins.slack import client, discovery
from move_forward.plugins.slack.repository import SlackRepository


def test_discovery_batches_missing_teams_and_preserves_known_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db = tmp_path / "d.db"
    config.write_text(
        f'email = "me@x"\ndb_path = "{db.as_posix()}"\nteams = ["bacon"]\n'
        '[slack]\n[slack.teams]\nbacon = "#chosen"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SLACK_API_TOKEN", "test-token")
    calls: list[str] = []

    def call(method: str, token: str, params: dict[str, str]) -> dict[str, Any]:
        assert method == "conversations.list" and token == "test-token"
        assert params["exclude_archived"] == "true"
        assert params["types"] == "public_channel,private_channel"
        calls.append(params["cursor"])
        if not params["cursor"]:
            # A short/empty page does not mean pagination is finished.
            return {"channels": [], "response_metadata": {"next_cursor": "next"}}
        return {
            "channels": [
                {"id": "C111111", "name": "tiger-team", "topic": {"value": "AI Agents"}},
                {"id": "C222222", "name": "team-web"},
                {"id": "C333333", "name": "team-gone", "is_archived": True},
                {"id": "C444444", "name": "help-web", "purpose": {"value": "Web team support"}},
            ]
        }

    monkeypatch.setattr(client, "call", call)
    prefix = ["-c", str(config), "slack"]
    assert cli.main([*prefix, "team", "set", "bacon", "#wrong"]) == 0
    capsys.readouterr()
    assert (
        cli.main(
            [
                *prefix,
                "destinations",
                "bacon",
                "ai-agents",
                "web",
                "gone",
                "web",
                "--discover",
                "--json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    teams = {t["slug"]: t for t in output["teams"]}
    assert len(output["teams"]) == 4 and output["people"] == []
    assert teams["bacon"]["channel_name"] == "#chosen" and teams["bacon"]["source"] == "config"
    assert teams["ai-agents"]["channel_id"] == "C111111"
    assert teams["ai-agents"]["candidates"][0]["matched_on"] == "topic"
    assert teams["ai-agents"]["source"] == "discovered"
    assert teams["web"]["status"] == "ambiguous" and teams["web"]["channel_id"] is None
    assert len(teams["web"]["candidates"]) == 2
    assert teams["gone"]["status"] == "missing"
    assert calls == ["", "next"]
    assert set(SlackRepository(Database(db)).team_channels()) == {"bacon"}
    # Known destinations don't need a token, even with --discover.
    monkeypatch.delenv("SLACK_API_TOKEN")
    assert cli.main([*prefix, "destinations", "bacon", "--discover"]) == 0
    assert calls == ["", "next"]


def test_ambiguous_channels_are_candidates_not_a_guessed_destination() -> None:
    matches = discovery.candidates(
        "web",
        [
            client.Channel(id="C111111", name="team-web"),
            client.Channel(id="C222222", name="support", purpose="For the web team"),
            client.Channel(id="C333333", name="website", topic="Websites"),
        ],
    )
    assert [m["channel_id"] for m in matches] == ["C222222", "C111111"]


def test_discovery_reports_scope_errors_and_plain_lookup_stays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "c.toml"
    config.write_text(
        f'email = "me@x"\ndb_path = "{(tmp_path / "d.db").as_posix()}"\n[slack]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SLACK_API_TOKEN", "test-token")

    def denied(*args: object) -> list[client.Channel]:
        raise client.SlackError("conversations.list", "missing_scope")

    monkeypatch.setattr(client, "channels", denied)
    prefix = ["-c", str(config), "slack", "destinations", "web"]
    assert cli.main([*prefix, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["teams"][0]["source"] is None
    assert cli.main([*prefix, "--discover"]) == 1
    assert "channels:read and groups:read" in capsys.readouterr().err


def test_repeated_channel_cursor_fails_instead_of_looping(monkeypatch: pytest.MonkeyPatch) -> None:
    def repeated(*args: object) -> dict[str, Any]:
        return {"channels": [], "response_metadata": {"next_cursor": "same"}}

    monkeypatch.setattr(client, "call", repeated)
    with pytest.raises(ValueError, match="repeated"):
        client.channels("test-token")
