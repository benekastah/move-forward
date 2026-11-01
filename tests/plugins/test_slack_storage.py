"""The slack plugin's tables: what its migration copies from core, and `mfd slack import`."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from skill_notes import REVIEWER_MAP, WORK_NOTES

from move_forward import cli
from move_forward.db import Database
from move_forward.plugins.slack import markdown
from move_forward.plugins.slack.models import Ping
from move_forward.plugins.slack.repository import MIGRATIONS, SlackRepository

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_the_migration_copies_what_core_held_for_slack() -> None:
    db = Database(":memory:")
    db.execute("INSERT INTO reviewer_users VALUES ('alice', 'a@x', 'U1', 'notes')")
    db.execute("INSERT INTO reviewer_users VALUES ('bob', 'b@x', NULL, 'lookup')")
    db.execute("INSERT INTO team_channels VALUES ('core', '#core', 'C1', 'notes')")
    db.execute(
        "INSERT INTO pings (pr_url, target, kind, message, sent_at, source)"
        " VALUES ('https://pr/1', 'user:alice', 'first', 'hi', ?, 'dashboard')",
        (NOW.isoformat(),),
    )
    db.migrate("slack", MIGRATIONS)
    slack = SlackRepository(db)
    assert {login: c.slack_id for login, c in slack.contacts().items()} == {"alice": "U1"}
    assert slack.team_channels()["core"].channel_id == "C1"
    assert [(p.target, p.message) for p in slack.pings()] == [("user:alice", "hi")]
    # The ping was sent from the dashboard, so an import doesn't replace it.
    slack.replace_imported([], [], [])
    assert slack.contacts() == {} and len(slack.pings()) == 1


def test_the_reviewer_map_and_logged_nudges_are_read() -> None:
    contacts, teams = markdown.read_reviewer_map(REVIEWER_MAP)
    assert [(c.login, c.slack_id) for c in contacts] == [("alice", "U111"), ("bob", None)]
    assert [(t.slug, t.channel_name, t.channel_id) for t in teams] == [
        ("core", "#team-core-chat", None),
        ("web", "#team-web", "C0123ABCD"),
    ]
    day = datetime(2026, 10, 29, tzinfo=UTC)
    assert [(p.pr_url, p.kind, p.sent_at, p.target) for p in markdown.read_nudges(WORK_NOTES)] == [
        ("https://github.com/acme/widgets/pull/12", "first", day, markdown.LOGGED_TARGET),
        ("https://github.com/acme/widgets/pull/13", "first", day, markdown.LOGGED_TARGET),
    ]


def test_mfd_slack_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    notes_dir = tmp_path / "skill"
    notes_dir.mkdir()
    (notes_dir / markdown.REVIEWER_MAP_FILE).write_text(REVIEWER_MAP)
    (notes_dir / markdown.WORK_NOTES_FILE).write_text(WORK_NOTES)
    config = tmp_path / "config.toml"
    config.write_text(f'email = "me@x"\ndb_path = "{(tmp_path / "d.db").as_posix()}"\n\n[slack]\n')
    assert cli.main(["-c", str(config), "slack", "import", str(notes_dir)]) == 0
    assert "logged nudges" in capsys.readouterr().out
    slack = SlackRepository(Database(tmp_path / "d.db"))
    slack.add_ping(Ping(pr_url="https://pr/9", target="user:x", kind="first", sent_at=NOW))
    imported = len(slack.pings()) - 1
    assert imported and slack.contacts()
    # Importing again replaces what the last import read, and keeps what the dashboard sent.
    assert cli.main(["-c", str(config), "slack", "import", str(notes_dir)]) == 0
    assert len(slack.pings()) == imported + 1
    assert cli.main(["-c", str(config), "slack", "import", str(tmp_path / "nope")]) == 1
