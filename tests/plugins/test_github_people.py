"""The github plugin's people: logins and commit emails, copied from core, imported from the
reviewer map, or looked up."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from skill_notes import REVIEWER_MAP

from move_forward import cli
from move_forward.db import Database
from move_forward.plugins.github.people import PeopleRepository, read_reviewer_map
from move_forward.plugins.github.tables import MIGRATIONS

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def test_the_migration_copies_emails_core_held() -> None:
    db = Database(":memory:")
    db.execute("INSERT INTO reviewer_users VALUES ('alice', 'a@x', 'U1', 'notes')")
    db.execute("INSERT INTO reviewer_users VALUES ('bob', NULL, NULL, 'lookup')")
    db.execute("INSERT INTO reviewer_users VALUES ('carol', NULL, 'U3', 'notes')")
    db.migrate("github", MIGRATIONS)
    # Bob's failed lookup is remembered, so it isn't tried again; Carol had only a Slack id.
    assert PeopleRepository(db).emails() == {"alice": "a@x", "bob": None}


def test_an_import_wins_over_lookups_and_lookups_never_overwrite() -> None:
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    people = PeopleRepository(db)
    people.save_looked_up("alice", "old@x", NOW)
    people.save_looked_up("bob", "b@x", NOW)
    people.replace_imported({"alice": "a@x"})
    people.save_looked_up("alice", "other@x", NOW)
    assert people.emails() == {"alice": "a@x", "bob": "b@x"}
    people.replace_imported({})
    assert people.emails() == {"bob": "b@x"}


def test_mfd_github_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "reviewer-map.md").write_text(REVIEWER_MAP)
    config = tmp_path / "config.toml"
    config.write_text(
        f'email = "me@x"\ndb_path = "{(tmp_path / "d.db").as_posix()}"\n\n[github]\norg = "acme"\n'
    )
    assert cli.main(["-c", str(config), "github", "import", str(tmp_path)]) == 0
    emails = read_reviewer_map(REVIEWER_MAP)
    assert emails == {"alice": "alice@example.com"}  # bob has no email
    assert "imported 1 reviewers' emails" in capsys.readouterr().out
    assert PeopleRepository(Database(tmp_path / "d.db")).emails() == emails
    assert cli.main(["-c", str(config), "github", "import", str(tmp_path / "nope")]) == 1


def test_lookups_that_found_no_email_are_tried_again_after_the_cutoff() -> None:
    db = Database(":memory:")
    db.execute("INSERT INTO reviewer_users VALUES ('dan', NULL, NULL, 'lookup')")  # no time
    db.migrate("github", MIGRATIONS)
    people = PeopleRepository(db)
    old = NOW - timedelta(days=100)
    people.save_looked_up("alice", "a@x", old)  # found: kept however old
    people.save_looked_up("bob", None, old)
    people.save_looked_up("carol", None, NOW)
    people.replace_imported({"erin": "e@x"})
    assert people.forget_misses_before(NOW - timedelta(days=90)) == 2  # bob and dan
    assert people.emails() == {"alice": "a@x", "carol": None, "erin": "e@x"}
