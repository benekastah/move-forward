"""Core's repositories: the queries over core's own tables. Plugins keep theirs."""

import hashlib
import secrets
from collections.abc import Callable, Collection
from datetime import datetime, timedelta

from pydantic import BaseModel, TypeAdapter

from .act_now import Feedback, FeedbackKey
from .db import Database
from .graph import Ref
from .panels import Layout

Identify = Callable[[str, str], str]
"""The id an entity has now, by kind and an id kept before: `Graph.identify`."""


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

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM kv WHERE plugin = ? AND key = ?", (self._owner, key))

    def keys(self, prefix: str = "") -> list[str]:
        """The keys set, those starting with `prefix`, in order."""
        rows = self._db.query(
            "SELECT key FROM kv WHERE plugin = ? AND substr(key, 1, ?) = ? ORDER BY key",
            (self._owner, len(prefix), prefix),
        )
        return [row["key"] for row in rows]

    def get_json[T](self, key: str, type_: type[T]) -> T | None:
        """The value at `key` read as JSON into `type_` (a model, or e.g. `list[str]`); None
        when it isn't set."""
        value = self.get(key)
        return None if value is None else TypeAdapter(type_).validate_json(value)

    def set_json(self, key: str, value: object) -> None:
        """Set `key` to `value` as JSON: a model, or lists, dicts and plain values."""
        self.set(key, TypeAdapter(type(value)).dump_json(value).decode())


class SessionRepository:
    """Browser sessions (`sessions`): a random id in the browser's cookie, kept here only as
    its hash, expiring, and revocable (`mfd logout`, a new token), unlike the token itself."""

    def __init__(self, db: Database):
        self._db = db

    @staticmethod
    def _hash(session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    def create(self, now: datetime, lasts: timedelta) -> str:
        """Start a session lasting `lasts`; its id, which only the browser is told."""
        session_id = secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (self._hash(session_id), now.isoformat(), (now + lasts).isoformat()),
        )
        return session_id

    def valid(self, session_id: str, now: datetime) -> bool:
        """Whether `session_id` names a session that hasn't expired or been revoked."""
        rows = self._db.query(
            "SELECT expires_at FROM sessions WHERE id_hash = ?", (self._hash(session_id),)
        )
        return bool(rows) and datetime.fromisoformat(rows[0]["expires_at"]) > now

    def revoke_all(self) -> int:
        """End every session; how many there were."""
        count = int(self._db.query("SELECT COUNT(*) AS n FROM sessions")[0]["n"])
        self._db.execute("DELETE FROM sessions")
        return count

    def prune(self, now: datetime) -> None:
        """Forget the expired ones."""
        self._db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))


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


class PriorityRepository:
    """How much I said entities matter (`priorities`): 0 not important, 1 important, 2
    critical, kept until the entity is done."""

    def __init__(self, db: Database):
        self._db = db

    def all(self) -> dict[Ref, int]:
        return {
            Ref(kind=row["kind"], id=row["id"]): row["level"]
            for row in self._db.query("SELECT * FROM priorities")
        }

    def set(self, ref: Ref, level: int, now: datetime) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO priorities VALUES (?, ?, ?, ?)",
            (ref.kind, ref.id, level, now.isoformat()),
        )

    def clear(self, ref: Ref) -> None:
        self._db.execute("DELETE FROM priorities WHERE kind = ? AND id = ?", (ref.kind, ref.id))

    def rekey(self, kinds: Collection[str], identify: Identify) -> int:
        """Give the priorities on entities of `kinds` the ids `identify` says they have now
        (see `Graph.identify`); one already set on the new id stays. How many changed."""
        return _rekey(self._db, "priorities", "kind", "id", kinds, identify)


class FeedbackRepository:
    """What I said of Act now's items (`act_now_feedback`): kept, pruned with `prune`."""

    def __init__(self, db: Database):
        self._db = db

    def all(self) -> dict[FeedbackKey, Feedback]:
        return {
            (row["subject_kind"], row["subject_id"], row["task"]): Feedback(
                action=row["action"],
                fingerprint=row["fingerprint"],
                until=datetime.fromisoformat(row["until"]) if row["until"] else None,
                said_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in self._db.query("SELECT * FROM act_now_feedback")
        }

    def say(self, key: FeedbackKey, feedback: Feedback, now: datetime) -> None:
        """Keep `feedback` on the item, in place of anything said of it before."""
        until = feedback.until.isoformat() if feedback.until else None
        self._db.execute(
            "INSERT OR REPLACE INTO act_now_feedback VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*key, feedback.action, until, feedback.fingerprint, now.isoformat()),
        )

    def forget(self, key: FeedbackKey) -> None:
        self._db.execute(
            "DELETE FROM act_now_feedback WHERE subject_kind = ? AND subject_id = ? AND task = ?",
            key,
        )

    def rekey(self, kinds: Collection[str], identify: Identify) -> int:
        """`PriorityRepository.rekey`, for what was said of items about `kinds`."""
        return _rekey(self._db, "act_now_feedback", "subject_kind", "subject_id", kinds, identify)

    def prune(self, now: datetime, before: datetime) -> int:
        """Drop snoozes that are over, and anything said before `before`; how many went."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM act_now_feedback WHERE until < ? OR created_at < ?",
                (now.isoformat(), before.isoformat()),
            ).rowcount


def _rekey(
    db: Database, table: str, kind: str, id: str, kinds: Collection[str], identify: Identify
) -> int:
    """Rewrite `table`'s ids of `kinds` (in its `kind` and `id` columns) to what `identify`
    says; where a row with the new id is there already, it stays and the old one goes."""
    marks = ", ".join("?" * len(kinds))
    rows = db.query(
        f"SELECT DISTINCT {kind}, {id} FROM {table} WHERE {kind} IN ({marks})", (*kinds,)
    )
    moves = [(r[kind], r[id], new) for r in rows if (new := identify(r[kind], r[id])) != r[id]]
    with db.transaction() as conn:
        for k, old, new in moves:
            conn.execute(
                f"UPDATE OR IGNORE {table} SET {id} = ? WHERE {kind} = ? AND {id} = ?",
                (new, k, old),
            )
            conn.execute(f"DELETE FROM {table} WHERE {kind} = ? AND {id} = ?", (k, old))
    return len(moves)
