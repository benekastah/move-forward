"""The slack plugin's tables: what its migrations copy and keep."""

from datetime import UTC, datetime

from move_forward.db import Database
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


def test_the_migration_keeps_imported_team_channels_as_set_by_hand() -> None:
    db = Database(":memory:")
    db.migrate("slack", MIGRATIONS[:3])
    db.execute("INSERT INTO slack_team_channels VALUES ('core', '#core', 'C1', 'import')")
    db.execute("INSERT INTO slack_team_channels VALUES ('web', '#web', NULL, 'dashboard')")
    db.migrate("slack", MIGRATIONS)
    slack = SlackRepository(db)
    assert {t: (c.channel_name, c.channel_id) for t, c in slack.team_channels().items()} == {
        "core": ("#core", "C1"),
        "web": ("#web", None),
    }
