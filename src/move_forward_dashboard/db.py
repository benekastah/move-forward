"""The SQLite connection and schema. Queries live in `repositories`."""

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
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

Params = tuple[object, ...]


class Database:
    """One SQLite connection, shared between threads under a lock."""

    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self.transaction() as conn:
            conn.executescript(SCHEMA)

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
