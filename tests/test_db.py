import sqlite3
from pathlib import Path

import pytest

from move_forward.db import BACKUPS_DIR, CORE, CORE_MIGRATIONS, V1_SCHEMA, Database
from move_forward.private import is_private
from move_forward.repositories import KvRepository

CREATE_A = "CREATE TABLE p_a (x INTEGER);"
CREATE_B = "CREATE TABLE p_b (x INTEGER);"


def _versions(db: Database) -> dict[str, int]:
    return {row["plugin"]: row["version"] for row in db.query("SELECT * FROM schema_versions")}


def test_a_database_from_before_migrations_upgrades_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO meta VALUES ('last_prune', '2026-10-30')")
    conn.execute("INSERT INTO reviewer_users VALUES ('alice', NULL, NULL, 'notes')")
    conn.commit()
    conn.close()

    db = Database(path)
    assert _versions(db) == {CORE: len(CORE_MIGRATIONS)}
    assert KvRepository(db, CORE).get("last_prune") == "2026-10-30"
    assert len(db.query("SELECT * FROM reviewer_users")) == 1
    for gone in ("meta", "work_items", "iced_stacks"):
        assert not db.query("SELECT name FROM sqlite_master WHERE name = ?", (gone,))


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


def test_snapshots_are_renamed_after_their_plugins(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO snapshots VALUES ('local', '2026-10-30', '{}', NULL)")
    conn.execute("INSERT INTO snapshots VALUES ('derived', '2026-10-30', '{}', NULL)")
    conn.commit()
    conn.close()
    sources = {row["source"] for row in Database(path).query("SELECT source FROM snapshots")}
    assert sources == {"git", "github.derived"}


def test_the_last_json_snapshots_are_cleared_and_when_they_were_fetched_kept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO snapshots VALUES ('github', '2026-10-30', '{\"mine\": []}', NULL)")
    conn.commit()
    conn.close()
    [row] = Database(path).query("SELECT * FROM snapshots")
    assert row["payload"] is None and row["fetched_at"] == "2026-10-30"


def _old_database(path: Path) -> None:
    """A database from before migrations, with a row in it."""
    conn = sqlite3.connect(path)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO work_items VALUES (0, '{}')")
    conn.commit()
    conn.close()


def _backups(path: Path) -> list[Path]:
    return sorted((path.parent / BACKUPS_DIR).glob(f"{path.name}.*"))


def test_a_backup_is_taken_once_before_pending_migrations(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.db"
    _old_database(path)
    db = Database(path)
    [backup] = _backups(path)
    copy = sqlite3.connect(backup)
    assert copy.execute("SELECT count(*) FROM work_items").fetchone() == (1,)
    assert copy.execute("SELECT name FROM sqlite_master WHERE name = 'kv'").fetchall() == []
    assert copy.execute("PRAGMA journal_mode").fetchone() == ("delete",)  # one file, no -wal
    copy.close()

    db.migrate("p", [CREATE_A])  # a plugin's migration in the same run: no second copy
    Database(path)  # nothing pending
    assert len(_backups(path)) == 1
    Database(path).migrate("p", [CREATE_A, CREATE_B])
    assert len(_backups(path)) == 2


def test_a_new_database_is_not_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.db"
    Database(path)
    assert _backups(path) == []


def test_only_the_newest_backups_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.db"
    _old_database(path)
    backups = tmp_path / BACKUPS_DIR
    backups.mkdir()
    for day in range(1, 5):
        (backups / f"dashboard.db.202601{day:02}T000000Z").write_text("old")
    Database(path, keep_backups=3)
    names = [p.name for p in _backups(path)]
    assert names[:2] == ["dashboard.db.20260103T000000Z", "dashboard.db.20260104T000000Z"]
    assert len(names) == 3


def test_keeping_no_backups_takes_none(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.db"
    _old_database(path)
    Database(path, keep_backups=0)
    assert _backups(path) == []


def test_foreign_keys_cascade(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    parent = "CREATE TABLE p_parent (id INTEGER PRIMARY KEY);"
    child = "CREATE TABLE p_child (parent INTEGER REFERENCES p_parent ON DELETE CASCADE);"
    db.migrate("p", [parent + child])
    db.execute("INSERT INTO p_parent VALUES (1)")
    db.execute("INSERT INTO p_child VALUES (1)")
    db.execute("DELETE FROM p_parent")
    assert db.query("SELECT * FROM p_child") == []
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO p_child VALUES (2)")


def test_a_file_database_lets_another_process_read_while_one_writes(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"


def test_the_database_and_its_backups_are_mine_alone(tmp_path: Path) -> None:
    path = tmp_path / "data" / "dashboard.db"
    db = Database(path)
    db.migrate("test", ["CREATE TABLE t (x);"])
    db.execute("INSERT INTO t VALUES (1)")
    assert is_private(path)
    again = Database(path)
    again.migrate("test", ["CREATE TABLE t (x);", "CREATE TABLE u (y);"])  # backs up first
    [backup] = (path.parent / "backups").iterdir()
    assert is_private(backup)
    assert is_private(path.parent / "backups")
