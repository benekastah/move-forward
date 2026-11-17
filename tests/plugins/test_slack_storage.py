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


def test_the_migration_names_the_teams_pinged_by_their_id() -> None:
    """A ping's team was its slug, in its PR's repo's owner; it's that team's id now. A team's
    channel was kept by slug alone, whose org nothing says: it's kept as it was."""
    db = Database(":memory:")
    db.migrate("slack", MIGRATIONS[:4])
    pr = "https://github.example.com/acme/widgets/pull/1"
    for target in ("team:web", "user:alice", "unknown (from work notes)"):
        db.execute(
            "INSERT INTO slack_pings (pr_url, target, kind, message, sent_at, source)"
            " VALUES (?, ?, 'first', '', ?, 'dashboard')",
            (pr, target, NOW.isoformat()),
        )
    db.execute("INSERT INTO slack_team_channels VALUES ('web', '#web', NULL)")
    db.migrate("slack", MIGRATIONS)
    slack = SlackRepository(db)
    assert [p.target for p in slack.pings()] == [
        "team:github.example.com/acme/web",
        "user:alice",
        "unknown (from work notes)",
    ]
    assert list(slack.team_channels()) == ["web"]


def test_the_migration_names_a_team_in_one_case_whatever_its_pr_url_says() -> None:
    """GitHub ignores case in an org's name: `Acme` in a PR's URL is `org = "acme"`'s."""
    db = Database(":memory:")
    db.migrate("slack", MIGRATIONS[:4])
    db.execute(
        "INSERT INTO slack_pings (pr_url, target, kind, message, sent_at, source)"
        " VALUES ('https://github.com/Acme/widgets/pull/1', 'team:Web', 'first', '', ?,"
        " 'dashboard')",
        (NOW.isoformat(),),
    )
    db.migrate("slack", MIGRATIONS)
    assert [p.target for p in SlackRepository(db).pings()] == ["team:github.com/acme/web"]


def test_the_migration_lower_cases_channels_kept_by_slug_so_they_can_be_cleared() -> None:
    """A channel set by hand was kept as typed (`Platform`); slack clears a team by its name
    lower-cased, as GitHub ignores case in it."""
    db = Database(":memory:")
    db.migrate("slack", MIGRATIONS[:4])
    db.execute("INSERT INTO slack_team_channels VALUES ('Platform', '#p', NULL)")
    db.execute("INSERT INTO slack_team_channels VALUES ('WEB', '#web-old', NULL)")
    db.execute("INSERT INTO slack_team_channels VALUES ('web', '#web', NULL)")
    db.migrate("slack", MIGRATIONS)
    slack = SlackRepository(db)
    assert sorted(slack.team_channels()) == ["platform", "web"]
    assert slack.team_channels()["web"].channel_name == "#web"  # the one already lower-cased
    assert slack.unset_team_channel("Platform")
    assert list(slack.team_channels()) == ["web"]
