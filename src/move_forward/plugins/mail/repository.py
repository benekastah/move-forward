"""The mail plugin's tables: the unread and flagged messages the last refresh found, and their
labels."""

import sqlite3
from datetime import datetime

from ...api import Database, Migration
from .models import MailSnapshot, Message

MIGRATIONS = [
    Migration(
        "0001_messages",
        """
    CREATE TABLE mail_messages (       -- fetched: unread and flagged messages, headers only
        uid INTEGER PRIMARY KEY,
        position INTEGER NOT NULL,     -- newest first
        message_id TEXT,
        thread TEXT NOT NULL,
        subject TEXT NOT NULL,
        sender_name TEXT NOT NULL,
        sender_email TEXT,
        to_me INTEGER NOT NULL,
        bulk INTEGER NOT NULL,
        flagged INTEGER NOT NULL,
        seen INTEGER NOT NULL,
        verified INTEGER NOT NULL,
        date TEXT NOT NULL
    );
    """,
    ),
    Migration(
        "0002_important",
        "ALTER TABLE mail_messages ADD COLUMN important INTEGER NOT NULL DEFAULT 0;",
    ),
    Migration(
        "0003_replies_to_me",
        "ALTER TABLE mail_messages ADD COLUMN replies_to_me INTEGER NOT NULL DEFAULT 0;",
    ),
    Migration(
        "0004_known", "ALTER TABLE mail_messages ADD COLUMN known INTEGER NOT NULL DEFAULT 0;"
    ),
    Migration(
        "0005_labels",
        """
    CREATE TABLE mail_message_labels ( -- fetched: their Gmail labels, Gmail's own and mine
        uid INTEGER NOT NULL,
        label TEXT NOT NULL,
        PRIMARY KEY (uid, label)
    );
    """,
    ),
    Migration(
        "0006_writers",
        """
    CREATE TABLE mail_thread_writers ( -- kept: who has written in each conversation, as only
        thread TEXT NOT NULL,          -- unread and flagged mail is fetched
        email TEXT NOT NULL,
        verified INTEGER NOT NULL,     -- the server has vouched for their From in it
        seen_at TEXT NOT NULL,         -- when their unread or flagged mail was last seen in it
        PRIMARY KEY (thread, email)
    );
    """,
    ),
]


class MessageRepository:
    """`mail_messages` and their `mail_message_labels`: replaced wholesale by each refresh."""

    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: MailSnapshot) -> None:
        """Keep `snapshot` in place of what was there, all at once: no one reads half of it, and
        a failure leaves the old one."""
        rows = [
            (
                m.uid,
                position,
                m.message_id,
                m.thread,
                m.subject,
                m.sender_name,
                m.sender_email,
                m.to_me,
                m.bulk,
                m.flagged,
                m.seen,
                m.verified,
                m.date.isoformat(),
                m.important,
                m.replies_to_me,
                m.known,
            )
            for position, m in enumerate(snapshot.messages)
        ]
        labels = [(m.uid, label) for m in snapshot.messages for label in dict.fromkeys(m.labels)]
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM mail_messages")
            conn.execute("DELETE FROM mail_message_labels")
            conn.executemany(
                "INSERT INTO mail_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.executemany("INSERT INTO mail_message_labels VALUES (?, ?)", labels)

    def load(self) -> MailSnapshot:
        labels = self._labels()
        return MailSnapshot(
            messages=[
                _message(row, labels.get(row["uid"], []))
                for row in self._db.query("SELECT * FROM mail_messages ORDER BY position")
            ]
        )

    def latest(self, thread: str) -> Message | None:
        """A conversation's newest message; None when the mailbox has none of it."""
        rows = self._db.query(
            "SELECT * FROM mail_messages WHERE thread = ? ORDER BY position LIMIT 1", (thread,)
        )
        if not rows:
            return None
        labels = self._db.query(
            "SELECT label FROM mail_message_labels WHERE uid = ? ORDER BY rowid", (rows[0]["uid"],)
        )
        return _message(rows[0], [row["label"] for row in labels])

    def _labels(self) -> dict[int, list[str]]:
        """Every message's labels by uid, as they came."""
        found: dict[int, list[str]] = {}
        for row in self._db.query("SELECT uid, label FROM mail_message_labels ORDER BY rowid"):
            found.setdefault(row["uid"], []).append(row["label"])
        return found


def _message(row: sqlite3.Row, labels: list[str]) -> Message:
    return Message(
        uid=row["uid"],
        message_id=row["message_id"],
        thread=row["thread"],
        subject=row["subject"],
        sender_name=row["sender_name"],
        sender_email=row["sender_email"],
        to_me=bool(row["to_me"]),
        bulk=bool(row["bulk"]),
        flagged=bool(row["flagged"]),
        seen=bool(row["seen"]),
        verified=bool(row["verified"]),
        important=bool(row["important"]),
        replies_to_me=bool(row["replies_to_me"]),
        known=bool(row["known"]),
        labels=labels,
        date=datetime.fromisoformat(row["date"]),
    )


class WriterRepository:
    """`mail_thread_writers`: who has written in each conversation (kept; `prune` clears
    conversations quiet a while)."""

    def __init__(self, db: Database):
        self._db = db

    def of(self, threads: list[str]) -> dict[str, dict[str, bool]]:
        """The writers of the conversations, by conversation, each with whether the server has
        vouched for them in it; none for one never seen."""
        found: dict[str, dict[str, bool]] = {}
        if not threads:
            return found
        marks = ", ".join("?" * len(threads))
        rows = self._db.query(
            f"SELECT thread, email, verified FROM mail_thread_writers WHERE thread IN ({marks})",
            tuple(threads),
        )
        for row in rows:
            found.setdefault(row["thread"], {})[row["email"]] = bool(row["verified"])
        return found

    def wrote(self, writers: list[tuple[str, str, bool]], now: datetime) -> None:
        """Keep that each `(thread, email, verified)` wrote in it, as of `now`; once vouched
        for in a conversation, they stay so."""
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO mail_thread_writers (thread, email, verified, seen_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (thread, email) DO UPDATE SET"
                " verified = MAX(verified, excluded.verified), seen_at = excluded.seen_at",
                [(t, e, int(v), now.isoformat()) for t, e, v in writers],
            )

    def prune(self, before: datetime) -> int:
        """Forget conversations no one has written in since `before`; how many writers went."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM mail_thread_writers WHERE thread IN (SELECT thread FROM"
                " mail_thread_writers GROUP BY thread HAVING MAX(julianday(seen_at)) < julianday(?))",
                (before.isoformat(),),
            ).rowcount
