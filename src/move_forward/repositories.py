"""Repositories: every SQL query, grouped by what it reads and writes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .db import Database
from .domain import IcedStack, Notes, Ping, ReviewerUser, TeamChannel, WorkItem

Source = Literal["notes", "lookup", "dashboard"]
SOURCE_NOTES: Source = "notes"
"""Rows imported from the skill's .md files; replaced on every import."""
SOURCE_LOOKUP: Source = "lookup"
"""Rows the dashboard looked up itself; kept across imports, but notes win."""
SOURCE_DASHBOARD: Source = "dashboard"
"""Rows recorded from the dashboard UI."""


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


class WorkItemRepository:
    def __init__(self, db: Database):
        self._db = db

    def all(self) -> list[WorkItem]:
        return [
            WorkItem.model_validate_json(row["item"])
            for row in self._db.query("SELECT item FROM work_items ORDER BY position")
        ]


class IcedStackRepository:
    def __init__(self, db: Database):
        self._db = db

    def all(self) -> list[IcedStack]:
        return [
            IcedStack.model_validate_json(row["stack"])
            for row in self._db.query("SELECT stack FROM iced_stacks ORDER BY bottom_branch")
        ]


class ReviewerRepository:
    """How to reach reviewers: users' emails and Slack IDs, teams' channels."""

    def __init__(self, db: Database):
        self._db = db

    def users(self) -> dict[str, ReviewerUser]:
        return {
            row["login"]: ReviewerUser(
                login=row["login"], email=row["email"], slack_id=row["slack_id"]
            )
            for row in self._db.query("SELECT * FROM reviewer_users")
        }

    def save_looked_up_email(self, login: str, email: str | None) -> None:
        """Cache a login's email. Never overwrites a row imported from the notes."""
        self._db.execute(
            "INSERT INTO reviewer_users VALUES (?, ?, NULL, ?) ON CONFLICT (login) DO NOTHING",
            (login, email, SOURCE_LOOKUP),
        )

    def teams(self) -> dict[str, TeamChannel]:
        return {
            row["slug"]: TeamChannel(
                slug=row["slug"], channel_name=row["channel_name"], channel_id=row["channel_id"]
            )
            for row in self._db.query("SELECT * FROM team_channels")
        }


_PING_INSERT = (
    "INSERT INTO pings (pr_url, target, kind, message, sent_at, source) VALUES (?, ?, ?, ?, ?, ?)"
)


def _ping_params(ping: Ping, source: Source) -> tuple[object, ...]:
    return (ping.pr_url, ping.target, ping.kind, ping.message, ping.sent_at.isoformat(), source)


class PingRepository:
    """Review pings sent from the dashboard, plus nudges the skill recorded."""

    def __init__(self, db: Database):
        self._db = db

    def add(self, ping: Ping) -> None:
        self._db.execute(_PING_INSERT, _ping_params(ping, SOURCE_DASHBOARD))

    def delete_before(self, cutoff: datetime) -> int:
        """Delete pings sent before `cutoff`; returns how many."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM pings WHERE sent_at < ?", (cutoff.isoformat(),)
            ).rowcount

    def all(self) -> list[Ping]:
        """Every recorded ping, oldest first."""
        return [
            Ping(
                pr_url=row["pr_url"],
                target=row["target"],
                kind=row["kind"],
                message=row["message"],
                sent_at=datetime.fromisoformat(row["sent_at"]),
            )
            for row in self._db.query("SELECT * FROM pings ORDER BY sent_at, id")
        ]


class NotesImportRepository:
    """Replaces everything imported from the skill's notes, across tables, in one transaction."""

    def __init__(self, db: Database):
        self._db = db

    def replace(self, notes: Notes) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM work_items")
            conn.execute("DELETE FROM iced_stacks")
            conn.execute("DELETE FROM reviewer_users WHERE source = ?", (SOURCE_NOTES,))
            conn.execute("DELETE FROM team_channels WHERE source = ?", (SOURCE_NOTES,))
            conn.execute("DELETE FROM pings WHERE source = ?", (SOURCE_NOTES,))
            conn.executemany(
                "INSERT INTO work_items VALUES (?, ?)",
                [(i, item.model_dump_json()) for i, item in enumerate(notes.work_items)],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO iced_stacks VALUES (?, ?, ?)",
                [(s.bottom_branch, s.repo_dir, s.model_dump_json()) for s in notes.iced_stacks],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO reviewer_users VALUES (?, ?, ?, ?)",
                [(u.login, u.email, u.slack_id, SOURCE_NOTES) for u in notes.users],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO team_channels VALUES (?, ?, ?, ?)",
                [(t.slug, t.channel_name, t.channel_id, SOURCE_NOTES) for t in notes.teams],
            )
            conn.executemany(_PING_INSERT, [_ping_params(p, SOURCE_NOTES) for p in notes.pings])


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
