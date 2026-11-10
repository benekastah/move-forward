"""Core's repositories: the queries over core's own tables. Plugins keep theirs."""

import hashlib
import secrets
import sqlite3
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
        """Every source's last run, enabled or not."""
        return self._rows(self._db.query("SELECT * FROM snapshots"))

    def of(self, plugins: Collection[str]) -> dict[str, SnapshotRow]:
        """The runs of `plugins` and their parts (`github.derived`), by source: not those of a
        plugin since taken out of the config, or removed, whose last run stays behind."""
        names = list(plugins)
        if not names:
            return {}
        marks = ", ".join("?" * len(names))
        # A part's source is `<plugin>.<part>`: its plugin is what comes before the first `.`,
        # compared exactly, case and all.
        plugin = (
            "CASE WHEN instr(source, '.') > 0 THEN substr(source, 1, instr(source, '.') - 1) END"
        )
        return self._rows(
            self._db.query(
                f"SELECT * FROM snapshots WHERE source IN ({marks}) OR {plugin} IN ({marks})",
                (*names, *names),
            )
        )

    @staticmethod
    def _rows(rows: list[sqlite3.Row]) -> dict[str, SnapshotRow]:
        return {
            row["source"]: SnapshotRow(
                fetched_at=datetime.fromisoformat(row["fetched_at"]), error=row["error"]
            )
            for row in rows
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
    """How much I said entities matter (`priorities`): 1 important or 2 critical, kept until
    the entity is done."""

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


class TagRepository:
    """The `#tags` I put on entities (`tags`), by handle: kept until I take them off, or the
    entity is gone where gone means done (`tags.forget_gone`)."""

    def __init__(self, db: Database):
        self._db = db

    def all(self) -> dict[Ref, list[str]]:
        found: dict[Ref, list[str]] = {}
        for row in self._db.query("SELECT * FROM tags ORDER BY created_at, tag"):
            found.setdefault(Ref(kind=row["kind"], id=row["id"]), []).append(row["tag"])
        return found

    def add(self, ref: Ref, tag: str, now: datetime) -> bool:
        """Whether it's new: one already on the entity stays as it was."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO tags VALUES (?, ?, ?, ?)",
                (ref.kind, ref.id, tag, now.isoformat()),
            )
            return cursor.rowcount == 1

    def clear(self, ref: Ref) -> None:
        """Take every tag off the entity."""
        self._db.execute("DELETE FROM tags WHERE kind = ? AND id = ?", (ref.kind, ref.id))

    def remove(self, ref: Ref, tag: str) -> bool:
        """Whether it was on the entity."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM tags WHERE kind = ? AND id = ? AND tag = ?", (ref.kind, ref.id, tag)
            )
            return cursor.rowcount == 1


class ContactRepository:
    """What I said people are to me (`contacts`), by address or `@domain`: kept until I clear
    it."""

    def __init__(self, db: Database):
        self._db = db

    def all(self) -> dict[str, str]:
        return {
            row["who"]: row["contact_type"]
            for row in self._db.query("SELECT * FROM contacts ORDER BY who")
        }

    def set(self, who: str, contact_type: str, now: datetime) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO contacts VALUES (?, ?, ?)",
            (who, contact_type, now.isoformat()),
        )

    def clear(self, who: str) -> bool:
        """Forget the type kept on `who`; whether there was one."""
        with self._db.transaction() as conn:
            return conn.execute("DELETE FROM contacts WHERE who = ?", (who,)).rowcount > 0


class ChangeState(BaseModel):
    """What core holds of a plugin's change checks (`change_checks`, `change_subjects`)."""

    fingerprint: str | None = None
    """The last answer's fingerprint; None before the first, or from a plugin without one."""
    checked_at: datetime | None = None
    """When core took that answer."""
    checks: int = 0
    """How many answers core has taken: the last one's number, which a collect starting now
    has seen everything up to."""
    subjects: frozenset[str] = frozenset()
    """What moved, by the checks since, that no collect started after has stored."""
    everything: bool = False
    """Whether something moved that no subject names, and no full collect started after has
    stored."""
    full_at: datetime | None = None
    """When the last full collect that stored started."""


class ChangeRepository:
    """What each plugin's change checks said moved, for the next check and collect (see
    `change_checks.ChangeChecks`). Each answer is numbered, and what it said moved is kept
    with its number, so a collect that stores settles only what moved before it started."""

    def __init__(self, db: Database):
        self._db = db

    def get(self, plugin: str) -> ChangeState:
        with self._db.reading():
            rows = self._db.query("SELECT * FROM change_checks WHERE plugin = ?", (plugin,))
            subjects = self._db.query(
                "SELECT subject FROM change_subjects WHERE plugin = ?", (plugin,)
            )
        found = frozenset(r["subject"] for r in subjects)
        if not rows:
            return ChangeState(subjects=found)
        row = rows[0]
        return ChangeState(
            fingerprint=row["fingerprint"],
            checked_at=row["checked_at"],
            checks=row["checks"],
            subjects=found,
            everything=row["everything"] is not None,
            full_at=row["full_at"],
        )

    def checked(
        self,
        plugin: str,
        fingerprint: str | None,
        at: datetime,
        subjects: Collection[str],
        everything: bool,
    ) -> None:
        """Keep an answer taken `at`, numbered after the last: its fingerprint in place of the
        last, and what it says moved with what's pending, by its number."""
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO change_checks (plugin, fingerprint, checked_at, checks, everything)"
                " VALUES (?1, ?2, ?3, 1, CASE WHEN ?4 THEN 1 END) ON CONFLICT (plugin) DO UPDATE"
                " SET fingerprint = excluded.fingerprint, checked_at = excluded.checked_at,"
                " checks = checks + 1,"
                " everything = CASE WHEN ?4 THEN checks + 1 ELSE everything END",
                (plugin, fingerprint, at.isoformat(), int(everything)),
            )
            [(number,)] = conn.execute(
                "SELECT checks FROM change_checks WHERE plugin = ?", (plugin,)
            ).fetchall()
            conn.executemany(
                "INSERT INTO change_subjects VALUES (?, ?, ?)"
                " ON CONFLICT (plugin, subject) DO UPDATE SET check_number = excluded.check_number",
                [(plugin, s, number) for s in subjects],
            )

    def collected(self, plugin: str, seen: int, full_at: datetime | None) -> None:
        """A collect that started once the answers up to number `seen` were taken stored: what
        they said moved is no longer pending, and when it was a full one (it started at
        `full_at`), neither is what no subject names."""
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM change_subjects WHERE plugin = ? AND check_number <= ?",
                (plugin, seen),
            )
            if full_at is not None:
                conn.execute(
                    "INSERT INTO change_checks (plugin, full_at) VALUES (?1, ?2)"
                    " ON CONFLICT (plugin) DO UPDATE SET full_at = excluded.full_at,"
                    " everything = CASE WHEN everything <= ?3 THEN NULL ELSE everything END",
                    (plugin, full_at.isoformat(), seen),
                )


class ProblemRepository:
    """What the plugins report wrong besides a failed refresh (`problems`), by plugin and the
    plugin's key: kept until the plugin clears it (`Context.problem`), or `prune` finds it
    hasn't been reported for a while."""

    def __init__(self, db: Database):
        self._db = db

    def set(self, plugin: str, key: str, text: str | None, now: datetime) -> str | None:
        """Keep `text` as the plugin's problem `key`, seen `now`, or forget it with None;
        returns what it was before."""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT text FROM problems WHERE plugin = ? AND key = ?", (plugin, key)
            ).fetchone()
            if text is None:
                conn.execute("DELETE FROM problems WHERE plugin = ? AND key = ?", (plugin, key))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO problems VALUES (?, ?, ?, ?)",
                    (plugin, key, text, now.isoformat()),
                )
        return None if row is None else row["text"]

    def prune(self, before: datetime) -> int:
        """Forget the problems last reported before `before`: those of a plugin since taken out
        of the config, or under a key it no longer uses; how many went."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM problems WHERE julianday(seen_at) < julianday(?)",
                (before.isoformat(),),
            ).rowcount

    def of(self, plugins: Collection[str]) -> dict[str, list[str]]:
        """The problems of `plugins`, by plugin, each's in the order of their keys: not those
        of a plugin since taken out of the config."""
        names = list(plugins)
        if not names:
            return {}
        found: dict[str, list[str]] = {}
        for row in self._db.query(
            f"SELECT plugin, text FROM problems WHERE plugin IN ({', '.join('?' * len(names))})"
            " ORDER BY plugin, key",
            tuple(names),
        ):
            found.setdefault(row["plugin"], []).append(row["text"])
        return found


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

    def bring_back(self, kind: str, id: str, task: str | None, now: datetime) -> int:
        """Forget the snoozes still running of the entity's items (`task`'s, or all its
        tasks'), leaving a pin or an open as it is; how many went."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM act_now_feedback WHERE subject_kind = ? AND subject_id = ?"
                " AND (? IS NULL OR task = ?)"
                # julianday: `until` keeps the local offset it was set with.
                " AND action = 'snooze' AND (until IS NULL OR julianday(until) > julianday(?))",
                (kind, id, task, task, now.isoformat()),
            ).rowcount

    def rekey(self, kinds: Collection[str], identify: Identify) -> int:
        """`PriorityRepository.rekey`, for what was said of items about `kinds`."""
        return _rekey(self._db, "act_now_feedback", "subject_kind", "subject_id", kinds, identify)

    def prune(self, now: datetime, before: datetime) -> int:
        """Drop snoozes that are over, and anything said before `before` (a snooze until the
        item changes, too); how many went."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM act_now_feedback"
                " WHERE julianday(until) < julianday(?) OR julianday(created_at) < julianday(?)",
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
