import sqlite3
from pathlib import Path

import pytest

from move_forward_dashboard.db import CORE, CORE_MIGRATIONS, V1_SCHEMA, Database
from move_forward_dashboard.repositories import KvRepository

CREATE_A = "CREATE TABLE p_a (x INTEGER);"
CREATE_B = "CREATE TABLE p_b (x INTEGER);"


def _versions(db: Database) -> dict[str, int]:
    return {row["plugin"]: row["version"] for row in db.query("SELECT * FROM schema_versions")}


def test_a_database_from_before_migrations_upgrades_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO meta VALUES ('last_prune', '2026-10-30')")
    conn.execute("INSERT INTO work_items VALUES (0, '{}')")
    conn.commit()
    conn.close()

    db = Database(path)
    assert _versions(db) == {CORE: len(CORE_MIGRATIONS)}
    assert KvRepository(db, CORE).get("last_prune") == "2026-10-30"
    assert len(db.query("SELECT * FROM work_items")) == 1
    assert not db.query("SELECT name FROM sqlite_master WHERE name = 'meta'")


def test_reopening_runs_nothing_again(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    Database(path).migrate("p", [CREATE_A])
    db = Database(path)  # core's migrations would fail if rerun: CREATE TABLE kv
    assert db.migrate("p", [CREATE_A]) == 0
    assert db.migrate("p", [CREATE_A, CREATE_B]) == 1
    assert _versions(db)["p"] == 2


def test_a_failing_migration_keeps_the_ones_before_it_and_records_neither_its_change() -> None:
    db = Database(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate("p", [CREATE_A, CREATE_B + " NOT SQL"])
    assert _versions(db)["p"] == 1
    assert not db.query("SELECT name FROM sqlite_master WHERE name = 'p_b'")
    db.execute("INSERT INTO p_a VALUES (1)")  # the connection is usable afterwards


def test_a_database_from_a_newer_version_is_refused() -> None:
    db = Database(":memory:")
    db.migrate("p", [CREATE_A, CREATE_B])
    with pytest.raises(RuntimeError, match="upgrade"):
        db.migrate("p", [CREATE_A])


def test_owner_names_are_checked() -> None:
    with pytest.raises(ValueError, match="owner"):
        Database(":memory:").migrate("p'); DROP TABLE kv; --", [CREATE_A])


def test_kv_is_namespaced_by_owner() -> None:
    db = Database(":memory:")
    KvRepository(db, "a").set("k", "1")
    KvRepository(db, "a").set("k", "2")
    assert KvRepository(db, "a").get("k") == "2"
    assert KvRepository(db, "b").get("k") is None
