"""Core's repositories: the queries over core's own tables. Plugins keep theirs."""

from datetime import datetime

from pydantic import BaseModel

from .db import Database
from .panels import Layout


class SnapshotRow(BaseModel):
    """A collector's last run: when, and why it failed if it did."""

    fetched_at: datetime
    error: str | None


class SnapshotRepository:
    """When each collector last ran, and its error. What they fetched lives in the plugins'
    tables; the old `payload` column stays only for the plugins' migrations to copy from."""

    def __init__(self, db: Database):
        self._db = db

    def save_fetched(self, source: str, fetched_at: datetime) -> None:
        """Record a good run: the time, and no error."""
        self._db.execute(
            "INSERT INTO snapshots (source, fetched_at) VALUES (?, ?)"
            " ON CONFLICT (source) DO UPDATE SET fetched_at = excluded.fetched_at, error = NULL",
            (source, fetched_at.isoformat()),
        )

    def save_error(self, source: str, fetched_at: datetime, error: str) -> None:
        """Record a failed run; what the last good one kept stays."""
        self._db.execute(
            "INSERT INTO snapshots (source, fetched_at, error) VALUES (?, ?, ?)"
            " ON CONFLICT (source) DO UPDATE SET fetched_at = excluded.fetched_at,"
            " error = excluded.error",
            (source, fetched_at.isoformat(), error),
        )

    def all(self) -> dict[str, SnapshotRow]:
        return {
            row["source"]: SnapshotRow(
                fetched_at=datetime.fromisoformat(row["fetched_at"]), error=row["error"]
            )
            for row in self._db.query("SELECT * FROM snapshots")
        }


class KvRepository:
    """Small key/value state, namespaced by owner: core (e.g. when the last prune ran) or a
    plugin, which only ever gets a repository over its own namespace."""

    def __init__(self, db: Database, owner: str):
        self._db = db
        self._owner = owner

    def get(self, key: str) -> str | None:
        rows = self._db.query(
            "SELECT value FROM kv WHERE plugin = ? AND key = ?", (self._owner, key)
        )
        return rows[0]["value"] if rows else None

    def set(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?)", (self._owner, key, value))


class LayoutRepository:
    """The page's order as someone dragged it (`layout`): kept, never pruned, cleared by
    Reset layout."""

    def __init__(self, db: Database):
        self._db = db

    def load(self) -> Layout:
        groups: list[str] = []
        panels: dict[str, list[str]] = {}
        for row in self._db.query("SELECT * FROM layout ORDER BY kind, parent, position"):
            if row["kind"] == "group":
                groups.append(row["key"])
            else:
                panels.setdefault(row["parent"], []).append(row["key"])
        return Layout(groups=groups, panels=panels)

    def save(self, layout: Layout) -> None:
        """Replace the stored order with `layout`."""
        rows = [("group", key, None, n) for n, key in enumerate(dict.fromkeys(layout.groups))]
        rows += [
            ("panel", key, group, n)
            for group, keys in layout.panels.items()
            for n, key in enumerate(dict.fromkeys(keys))
        ]
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM layout")
            conn.executemany("INSERT OR IGNORE INTO layout VALUES (?, ?, ?, ?)", rows)

    def reset(self) -> None:
        self._db.execute("DELETE FROM layout")
