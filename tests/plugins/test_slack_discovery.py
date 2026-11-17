"""Explicit channel discovery is batched, read-only, and reports ambiguous matches."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import NOW

from move_forward import cli
from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref
from move_forward.kinds import TEAM
from move_forward.plugin import Context
from move_forward.plugins.slack import client, discovery
from move_forward.plugins.slack.cli import list_destinations
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.repository import MIGRATIONS, SlackRepository
from move_forward.repositories import KvRepository

ACME = "github.com/acme"


def test_discovery_batches_missing_teams_and_preserves_known_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db = tmp_path / "d.db"
    config.write_text(
        f'email = "me@x"\ndb_path = "{db.as_posix()}"\n[slack]\n[slack.teams]\n"{ACME}/bacon" = "#chosen"\n',
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
    assert cli.main([*prefix, "team", "set", f"{ACME}/bacon", "#wrong"]) == 0
    capsys.readouterr()
    assert (
        cli.main(
            [
                *prefix,
                "destinations",
                *(f"{ACME}/{slug}" for slug in ("bacon", "ai-agents", "web", "gone", "web")),
                "--discover",
                "--json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    teams = {t["team"].removeprefix(f"{ACME}/"): t for t in output["teams"]}
    assert len(output["teams"]) == 4 and output["people"] == []
    assert teams["bacon"]["channel_name"] == "#chosen" and teams["bacon"]["source"] == "config"
    assert teams["ai-agents"]["channel_id"] == "C111111"
    assert teams["ai-agents"]["candidates"][0]["matched_on"] == "topic"
    assert teams["ai-agents"]["source"] == "discovered"
    assert teams["web"]["status"] == "ambiguous" and teams["web"]["channel_id"] is None
    assert len(teams["web"]["candidates"]) == 2
    assert teams["gone"]["status"] == "missing"
    assert calls == ["", "next"]
    assert set(SlackRepository(Database(db)).team_channels()) == {f"{ACME}/bacon"}
    # Known destinations don't need a token, even with --discover.
    monkeypatch.delenv("SLACK_API_TOKEN")
    assert cli.main([*prefix, "destinations", f"{ACME}/bacon", "--discover"]) == 0
    assert calls == ["", "next"]


def _team(id: str, mine: bool) -> Entity:
    return Entity(
        ref=Ref(kind=TEAM, id=id), title=id, attrs={"handle": id.rpartition("/")[2], "mine": mine}
    )


def test_discovery_looks_for_my_teams_from_the_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(":memory:")
    db.migrate("slack", MIGRATIONS)
    ctx = Context(
        config=SlackConfig(),
        email="me@x",
        prune_days=90,
        now=NOW,
        db=db,
        kv=KvRepository(db, "slack"),
    )
    monkeypatch.setenv("SLACK_API_TOKEN", "test-token")

    def channels(token: str) -> list[client.Channel]:
        return [client.Channel(id="C111111", name="team-web")]

    monkeypatch.setattr(client, "channels", channels)
    graph = Graph([_team("github.com/acme/web", True), _team("github.com/acme/ops", False)])
    found = json.loads(list_destinations(ctx, graph, True, [], True))
    assert [(t["team"], t["channel_id"]) for t in found["teams"]] == [(f"{ACME}/web", "C111111")]
    # A slug that's no GitHub slug, from some other plugin's team, is looked for too, not
    # blamed on teams nobody passed.
    odd = Graph([_team(f"{ACME}/web", True), _team("gitlab.com/acme/eng.team", True)])
    found = json.loads(list_destinations(ctx, odd, True, [], True))
    assert [t["team"] for t in found["teams"]] == [f"{ACME}/web", "gitlab.com/acme/eng.team"]


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
    prefix = ["-c", str(config), "slack", "destinations", f"{ACME}/web"]
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
