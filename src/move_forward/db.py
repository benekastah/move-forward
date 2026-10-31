"""The SQLite connection and migrations. Queries live in `repositories`.

Core and every plugin own a list of migrations, SQL scripts applied in order. How many of
each have run is kept in `schema_versions`, so a migration runs once per database and new
ones are appended, never edited.
"""

import logging
import re
import sqlite3
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

CORE = "core"
"""The `schema_versions` key of core's own migrations."""
OWNER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""What a migrations owner may be called; owner names are written into SQL."""
BACKUPS_DIR = "backups"
"""Next to the database: copies taken before migrations, `<db name>.<UTC time>`."""
BACKUP_TIME_FORMAT = "%Y%m%dT%H%M%S.%fZ"
"""Sorts by time as text, so the oldest backups sort first."""
DEFAULT_KEEP_BACKUPS = 10
SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")
"""Files SQLite keeps beside a database; they belong to it, they aren't backups of their own."""
BUSY_TIMEOUT_SECONDS = 10
"""How long a write waits for another process's (e.g. `mfd notes` while the server runs)."""

V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    source TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    payload TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS work_items (
    position INTEGER PRIMARY KEY,
    item TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iced_stacks (
    bottom_branch TEXT NOT NULL,
    repo_dir TEXT NOT NULL,
    stack TEXT NOT NULL,
    PRIMARY KEY (bottom_branch, repo_dir)
);
CREATE TABLE IF NOT EXISTS reviewer_users (
    login TEXT PRIMARY KEY,
    email TEXT,
    slack_id TEXT,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_channels (
    slug TEXT PRIMARY KEY,
    channel_name TEXT,
    channel_id TEXT,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pings (
    id INTEGER PRIMARY KEY,
    pr_url TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pings_pr_url ON pings (pr_url);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
"""The schema before migrations. `IF NOT EXISTS` lets databases made before them upgrade."""

_V2_KV_AND_LAYOUT = """
CREATE TABLE kv (
    plugin TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (plugin, key)
);
INSERT INTO kv SELECT 'core', key, value FROM meta;
DROP TABLE meta;
CREATE TABLE layout (
    kind TEXT NOT NULL CHECK (kind IN ('group', 'panel')),
    key TEXT NOT NULL,
    parent TEXT,
    position INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

_V3_PLUGIN_SOURCES = """
UPDATE snapshots SET source = 'git' WHERE source = 'local';
UPDATE snapshots SET source = 'github.derived' WHERE source = 'derived';
"""
"""Snapshots are named after the plugin that stores them."""

_V4_NOTES_IN_THE_NOTES_PLUGIN = """
DROP TABLE work_items;
DROP TABLE iced_stacks;
"""
"""Core's copies of the markdown notes; the notes plugin's own tables hold them now."""

CORE_MIGRATIONS = [V1_SCHEMA, _V2_KV_AND_LAYOUT, _V3_PLUGIN_SOURCES, _V4_NOTES_IN_THE_NOTES_PLUGIN]

_SCHEMA_VERSIONS = """
CREATE TABLE IF NOT EXISTS schema_versions (
    plugin TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
"""

Params = tuple[object, ...]


class Database:
    """One SQLite connection, shared between threads under a lock."""

    def __init__(self, path: Path | str, keep_backups: int = DEFAULT_KEEP_BACKUPS):
        """`keep_backups`: how many copies taken before migrations to keep; 0 takes none."""
        self._path = None if path == ":memory:" else Path(path)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._keep_backups = keep_backups
        self._backed_up = not (self._path and self._path.exists() and self._path.stat().st_size)
        """Whether there's no need for a backup: one was taken, or there's nothing to lose."""
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=BUSY_TIMEOUT_SECONDS
        )
        self._conn.row_factory = sqlite3.Row
        # Foreign keys are off in SQLite unless asked for, and `ON DELETE CASCADE` needs them.
        # (A migration that rebuilds a table someone references must say so; see `migrate`.)
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path:
            # Readers don't block the writer, so other processes can use it while we do.
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA_VERSIONS)
        self.migrate(CORE, CORE_MIGRATIONS)

    def migrate(self, owner: str, migrations: Sequence[str]) -> int:
        """Apply `owner`'s migrations that haven't run yet, each in its own transaction.

        Foreign keys are enforced, and SQLite can't turn them off inside a transaction, so a
        migration that rebuilds a referenced table copies it under a new name and renames,
        rather than dropping the original first.

        Returns how many ran. Fails if the database has run more than `migrations` holds,
        i.e. it was used by a newer version of the dashboard.
        """
        if not OWNER_RE.match(owner):
            raise ValueError(f"bad migrations owner {owner!r}")
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM schema_versions WHERE plugin = ?", (owner,)
            ).fetchone()
            done: int = row["version"] if row else 0
            if done > len(migrations):
                raise RuntimeError(
                    f"the database has {done} {owner} migrations but this version knows"
                    f" {len(migrations)}; upgrade the dashboard"
                )
            if done < len(migrations):
                self._backup()
            for version, sql in enumerate(migrations[done:], start=done + 1):
                # executescript commits any open transaction first, so the script carries
                # its own, which records the version together with the change.
                try:
                    self._conn.executescript(
                        f"BEGIN;\n{sql}\nINSERT OR REPLACE INTO schema_versions"
                        f" VALUES ('{owner}', {version});\nCOMMIT;"
                    )
                except sqlite3.Error:
                    if self._conn.in_transaction:
                        self._conn.rollback()
                    raise
            return len(migrations) - done

    def _backup(self) -> None:
        """Copy the database into `BACKUPS_DIR` before its first migration in this process,
        then delete all but the newest `keep_backups` copies. Call with the lock held."""
        if self._backed_up or self._path is None or self._keep_backups <= 0:
            return
        self._backed_up = True
        backups = self._path.parent / BACKUPS_DIR
        backups.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime(BACKUP_TIME_FORMAT)
        target = backups / f"{self._path.name}.{stamp}"
        copy = sqlite3.connect(target)
        try:
            self._conn.backup(copy)
            # The copy inherits WAL mode; a rollback journal keeps it one self-contained file
            # (no -wal and -shm beside it once something opens it), which is what a backup is.
            (mode,) = copy.execute("PRAGMA journal_mode = DELETE").fetchone()
        finally:
            copy.close()
        if mode == "delete":  # everything is in the file now; SQLite can leave an empty -shm
            for suffix in SQLITE_SIDE_FILES:
                target.with_name(target.name + suffix).unlink(missing_ok=True)
        log.info("backed up %s to %s before migrating", self._path, target)
        copies = sorted(
            path
            for path in backups.glob(f"{self._path.name}.*")
            if not path.name.endswith(SQLITE_SIDE_FILES)
        )
        for path in copies[: -self._keep_backups]:
            path.unlink()
            for suffix in SQLITE_SIDE_FILES:
                path.with_name(path.name + suffix).unlink(missing_ok=True)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """The connection, locked, in a transaction that commits on success."""
        with self._lock, self._conn:
            yield self._conn

    def query(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        with self.transaction() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: Params = ()) -> None:
        with self.transaction() as conn:
            conn.execute(sql, params)
