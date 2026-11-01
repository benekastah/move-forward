"""Core's repositories: the queries over core's own tables. Plugins keep theirs."""

from datetime import datetime

from pydantic import BaseModel

from .db import Database


class SnapshotRow(BaseModel):
    """A collector's latest result. `payload` is None until its first success."""

    fetched_at: datetime
    payload: str | None
    """The snapshot model as JSON."""
    error: str | None


class SnapshotRepository:
    """The latest result of each collector, as JSON."""

    def __init__(self, db: Database):
        self._db = db

    def save(self, source: str, fetched_at: datetime, payload: BaseModel) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, NULL)",
            (source, fetched_at.isoformat(), payload.model_dump_json()),
        )

    def save_fetched(self, source: str, fetched_at: datetime) -> None:
        """Record a good refresh whose result the plugin stored in its own tables: the time,
        and no error. Any payload stays as it was."""
        self._db.execute(
            "INSERT INTO snapshots (source, fetched_at) VALUES (?, ?)"
            " ON CONFLICT (source) DO UPDATE SET fetched_at = excluded.fetched_at, error = NULL",
            (source, fetched_at.isoformat()),
        )

    def save_error(self, source: str, fetched_at: datetime, error: str) -> None:
        """Record a failed refresh. The previous payload, if any, is kept."""
        self._db.execute(
            "INSERT INTO snapshots (source, fetched_at, error) VALUES (?, ?, ?)"
            " ON CONFLICT (source) DO UPDATE SET fetched_at = excluded.fetched_at,"
            " error = excluded.error",
            (source, fetched_at.isoformat(), error),
        )

    def all(self) -> dict[str, SnapshotRow]:
        return {
            row["source"]: SnapshotRow(
                fetched_at=datetime.fromisoformat(row["fetched_at"]),
                payload=row["payload"],
                error=row["error"],
            )
            for row in self._db.query("SELECT * FROM snapshots")
        }

    def get[T: BaseModel](self, source: str, model: type[T]) -> T | None:
        rows = self._db.query("SELECT payload FROM snapshots WHERE source = ?", (source,))
        if not rows or rows[0]["payload"] is None:
            return None
        return model.model_validate_json(rows[0]["payload"])


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
