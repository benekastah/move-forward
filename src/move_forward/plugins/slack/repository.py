"""The slack plugin's tables, and the queries over them."""

from datetime import datetime
from typing import Literal

from ...db import Database
from .models import Contact, Ping, TeamChannel

Source = Literal["import", "dashboard"]
IMPORTED: Source = "import"
"""Rows `mfd slack import` read from the skill's markdown; an import replaces them."""
DASHBOARD: Source = "dashboard"
"""Rows the dashboard wrote, e.g. pings marked as sent; an import leaves them."""

MIGRATIONS = [
    """
    CREATE TABLE slack_contacts (      -- kept: where a person's pings go
        login TEXT PRIMARY KEY,
        slack_id TEXT,
        source TEXT NOT NULL CHECK (source IN ('import', 'dashboard'))
    );
    CREATE TABLE slack_team_channels ( -- kept: where a team's pings go
        team TEXT PRIMARY KEY,
        channel_name TEXT,
        channel_id TEXT,
        source TEXT NOT NULL CHECK (source IN ('import', 'dashboard'))
    );
    CREATE TABLE slack_pings (         -- kept: pings sent
        id INTEGER PRIMARY KEY,
        pr_url TEXT NOT NULL,
        target TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('first', 'follow-up')),
        message TEXT NOT NULL,
        sent_at TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('import', 'dashboard'))
    );
    CREATE INDEX slack_pings_pr_url ON slack_pings (pr_url);
    -- What core's tables held for slack before it had its own; core keeps them, emptied.
    INSERT INTO slack_contacts
        SELECT login, slack_id, CASE source WHEN 'dashboard' THEN 'dashboard' ELSE 'import' END
        FROM reviewer_users WHERE slack_id IS NOT NULL;
    INSERT INTO slack_team_channels
        SELECT slug, channel_name, channel_id,
            CASE source WHEN 'dashboard' THEN 'dashboard' ELSE 'import' END
        FROM team_channels;
    INSERT INTO slack_pings (pr_url, target, kind, message, sent_at, source)
        SELECT pr_url, target, kind, message, sent_at,
            CASE source WHEN 'dashboard' THEN 'dashboard' ELSE 'import' END
        FROM pings;
    """
]
"""The slack plugin's migrations. Every table is kept: only the dashboard knows these."""


class SlackRepository:
    """People's and teams' Slack destinations, and the pings sent."""

    def __init__(self, db: Database):
        self._db = db

    def contacts(self) -> dict[str, Contact]:
        return {
            row["login"]: Contact(login=row["login"], slack_id=row["slack_id"])
            for row in self._db.query("SELECT * FROM slack_contacts")
        }

    def team_channels(self) -> dict[str, TeamChannel]:
        return {
            row["team"]: TeamChannel(
                slug=row["team"], channel_name=row["channel_name"], channel_id=row["channel_id"]
            )
            for row in self._db.query("SELECT * FROM slack_team_channels")
        }

    def pings(self) -> list[Ping]:
        """Every ping sent, oldest first."""
        return [
            Ping(
                pr_url=row["pr_url"],
                target=row["target"],
                kind=row["kind"],
                message=row["message"],
                sent_at=datetime.fromisoformat(row["sent_at"]),
            )
            for row in self._db.query("SELECT * FROM slack_pings ORDER BY sent_at, id")
        ]

    def add_ping(self, ping: Ping) -> None:
        self._db.execute(
            "INSERT INTO slack_pings (pr_url, target, kind, message, sent_at, source)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                ping.pr_url,
                ping.target,
                ping.kind,
                ping.message,
                ping.sent_at.isoformat(),
                DASHBOARD,
            ),
        )

    def delete_pings_before(self, cutoff: datetime) -> int:
        """Delete pings sent before `cutoff`; returns how many."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM slack_pings WHERE sent_at < ?", (cutoff.isoformat(),)
            ).rowcount

    def replace_imported(
        self, contacts: list[Contact], teams: list[TeamChannel], pings: list[Ping]
    ) -> None:
        """Replace what an earlier import read with these; what the dashboard wrote stays, and
        wins where both name the same person or team."""
        with self._db.transaction() as conn:
            for table in ("slack_contacts", "slack_team_channels", "slack_pings"):
                conn.execute(f"DELETE FROM {table} WHERE source = ?", (IMPORTED,))
            conn.executemany(
                "INSERT OR IGNORE INTO slack_contacts VALUES (?, ?, ?)",
                [(c.login, c.slack_id, IMPORTED) for c in contacts],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO slack_team_channels VALUES (?, ?, ?, ?)",
                [(t.slug, t.channel_name, t.channel_id, IMPORTED) for t in teams],
            )
            conn.executemany(
                "INSERT INTO slack_pings (pr_url, target, kind, message, sent_at, source)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (p.pr_url, p.target, p.kind, p.message, p.sent_at.isoformat(), IMPORTED)
                    for p in pings
                ],
            )
