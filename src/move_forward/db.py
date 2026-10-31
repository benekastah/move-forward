"""The SQLite connection and migrations. Queries live in `repositories`.

Core and every plugin own a list of migrations, SQL scripts applied in order. How many of
each have run is kept in `schema_versions`, so a migration runs once per database and new
ones are appended, never edited.
"""

import re
import sqlite3
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

CORE = "core"
"""The `schema_versions` key of core's own migrations."""
OWNER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""What a migrations owner may be called; owner names are written into SQL."""

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

CORE_MIGRATIONS = [V1_SCHEMA, _V2_KV_AND_LAYOUT, _V3_PLUGIN_SOURCES]

_SCHEMA_VERSIONS = """
CREATE TABLE IF NOT EXISTS schema_versions (
    plugin TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
"""

Params = tuple[object, ...]


class Database:
    """One SQLite connection, shared between threads under a lock."""

    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA_VERSIONS)
        self.migrate(CORE, CORE_MIGRATIONS)

    def migrate(self, owner: str, migrations: Sequence[str]) -> int:
        """Apply `owner`'s migrations that haven't run yet, each in its own transaction.

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
