"""The mail plugin's table: the unread and flagged messages the last refresh found."""

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
]


class MessageRepository:
    """`mail_messages`: replaced wholesale by each refresh."""

    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: MailSnapshot) -> None:
        self._db.execute("DELETE FROM mail_messages")
        for position, m in enumerate(snapshot.messages):
            self._db.execute(
                "INSERT INTO mail_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )

    def load(self) -> MailSnapshot:
        return MailSnapshot(
            messages=[
                _message(row)
                for row in self._db.query("SELECT * FROM mail_messages ORDER BY position")
            ]
        )

    def latest(self, thread: str) -> Message | None:
        """A conversation's newest message; None when the mailbox has none of it."""
        rows = self._db.query(
            "SELECT * FROM mail_messages WHERE thread = ? ORDER BY position LIMIT 1", (thread,)
        )
        return _message(rows[0]) if rows else None


def _message(row: sqlite3.Row) -> Message:
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
        date=datetime.fromisoformat(row["date"]),
    )
