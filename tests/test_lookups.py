"""`api.LookupCache`: a plugin's kept cache of looked-up values, which an import or a hand can
also set."""

from datetime import UTC, datetime, timedelta

import pytest

from move_forward.db import Database, Migration
from move_forward.lookups import BY_HAND, IMPORTED, LOOKED_UP, LookupCache

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
TABLE = Migration(
    "0001_ids",
    """
    CREATE TABLE demo_ids (
        login TEXT PRIMARY KEY,
        user_id TEXT,
        source TEXT NOT NULL CHECK (source IN ('dashboard', 'import', 'lookup')),
        looked_up_at TEXT
    );
    """,
)


def _cache() -> LookupCache:
    db = Database(":memory:")
    db.migrate("demo", [TABLE])
    return LookupCache(db, "demo_ids", "login", "user_id")


def test_by_hand_wins_over_an_import_which_wins_over_a_lookup() -> None:
    ids = _cache()
    ids.save_looked_up("alice", "L1", NOW)
    ids.save_looked_up("bob", "L2", NOW)
    ids.set_by_hand("bob", "H2")
    ids.replace_imported({"alice": "I1", "bob": "I2", "carol": "I3"})
    ids.save_looked_up("carol", "L3", NOW)  # a lookup never overwrites
    assert ids.values() == {"alice": "I1", "bob": "H2", "carol": "I3"}
    assert ids.sources() == {"alice": IMPORTED, "bob": BY_HAND, "carol": IMPORTED}
    # The next import replaces what the last one read, and leaves the rest.
    ids.replace_imported({"dan": "I4"})
    assert ids.values() == {"bob": "H2", "dan": "I4"}
    assert ids.forget("bob") and not ids.forget("bob")
    assert ids.values() == {"dan": "I4"}


def test_lookups_that_found_nothing_are_forgotten_after_the_cutoff() -> None:
    ids = _cache()
    old = NOW - timedelta(days=100)
    ids.save_looked_up("alice", "L1", old)  # found: kept however old
    ids.save_looked_up("bob", None, old)
    ids.save_looked_up("carol", None, NOW)
    ids.set_by_hand("dan", None)  # set to nothing by hand: no lookup to try again
    assert ids.forget_misses_before(NOW - timedelta(days=90)) == 1
    assert ids.values() == {"alice": "L1", "carol": None, "dan": None}
    assert ids.sources()["carol"] == LOOKED_UP


def test_an_import_joins_the_callers_transaction() -> None:
    db = Database(":memory:")
    db.migrate("demo", [TABLE])
    ids = LookupCache(db, "demo_ids", "login", "user_id")
    with pytest.raises(RuntimeError), db.transaction() as conn:
        ids.replace_imported({"alice": "I1"}, conn)
        raise RuntimeError("the caller's next write fails")
    assert ids.values() == {}


def test_names_are_refused_unless_they_are_plain_identifiers() -> None:
    db = Database(":memory:")
    with pytest.raises(ValueError, match="isn't a table or column name"):
        LookupCache(db, "demo_ids; DROP TABLE kv", "login", "user_id")
