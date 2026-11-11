"""The github plugin's people: logins and commit emails, copied from core or looked up."""

from datetime import UTC, datetime, timedelta

from move_forward.db import Database
from move_forward.plugins.github.people import PeopleRepository
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


def test_a_lookup_never_overwrites_what_is_known() -> None:
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    people = PeopleRepository(db)
    people.save_looked_up("alice", "a@x", NOW)
    people.save_looked_up("alice", "other@x", NOW)
    assert people.emails() == {"alice": "a@x"}


def test_lookups_that_found_no_email_are_tried_again_after_the_cutoff() -> None:
    db = Database(":memory:")
    db.execute("INSERT INTO reviewer_users VALUES ('dan', NULL, NULL, 'lookup')")  # no time
    db.migrate("github", MIGRATIONS)
    people = PeopleRepository(db)
    old = NOW - timedelta(days=100)
    people.save_looked_up("alice", "a@x", old)  # found: kept however old
    people.save_looked_up("bob", None, old)
    people.save_looked_up("carol", None, NOW)
    people.save_looked_up("erin", "e@x", NOW)
    assert people.forget_misses_before(NOW - timedelta(days=90)) == 2  # bob and dan
    assert people.emails() == {"alice": "a@x", "carol": None, "erin": "e@x"}
