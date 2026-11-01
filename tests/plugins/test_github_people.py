"""The github plugin's people: logins and commit emails, copied from core, imported from the
reviewer map, or looked up."""

from pathlib import Path

import pytest
from skill_notes import REVIEWER_MAP

from move_forward import cli
from move_forward.db import Database
from move_forward.plugins.github.people import PeopleRepository, read_reviewer_map
from move_forward.plugins.github.tables import MIGRATIONS


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
    people.save_looked_up("alice", "old@x")
    people.save_looked_up("bob", "b@x")
    people.replace_imported({"alice": "a@x"})
    people.save_looked_up("alice", "other@x")
    assert people.emails() == {"alice": "a@x", "bob": "b@x"}
    people.replace_imported({})
    assert people.emails() == {"bob": "b@x"}


def test_mfd_github_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "reviewer-map.md").write_text(REVIEWER_MAP)
    config = tmp_path / "config.toml"
    config.write_text(
        f'email = "me@x"\ndb_path = "{tmp_path / "d.db"}"\n\n[github]\norg = "acme"\n'
    )
    assert cli.main(["-c", str(config), "github", "import", str(tmp_path)]) == 0
    emails = read_reviewer_map(REVIEWER_MAP)
    assert emails == {"alice": "alice@example.com"}  # bob has no email
    assert "imported 1 reviewers' emails" in capsys.readouterr().out
    assert PeopleRepository(Database(tmp_path / "d.db")).emails() == emails
    assert cli.main(["-c", str(config), "github", "import", str(tmp_path / "nope")]) == 1
