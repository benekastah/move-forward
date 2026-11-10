"""`api.LookupCache`: a plugin's kept cache of looked-up values, which a hand can also set."""

from datetime import UTC, datetime, timedelta

import pytest

from move_forward.db import Database, Migration
from move_forward.lookups import BY_HAND, LOOKED_UP, LookupCache

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


def test_by_hand_wins_over_a_lookup_which_never_overwrites() -> None:
    ids = _cache()
    ids.save_looked_up("alice", "L1", NOW)
    ids.save_looked_up("bob", "L2", NOW)
    ids.set_by_hand("bob", "H2")
    ids.save_looked_up("alice", "L3", NOW)
    assert ids.values() == {"alice": "L1", "bob": "H2"}
    assert ids.sources() == {"alice": LOOKED_UP, "bob": BY_HAND}
    assert ids.forget("bob") and not ids.forget("bob")
    assert ids.values() == {"alice": "L1"}


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


def test_names_are_refused_unless_they_are_plain_identifiers() -> None:
    db = Database(":memory:")
    with pytest.raises(ValueError, match="isn't a table or column name"):
        LookupCache(db, "demo_ids; DROP TABLE kv", "login", "user_id")
