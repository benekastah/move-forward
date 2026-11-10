"""The slack plugin's tables, and the queries over them."""

from datetime import datetime

from ...api import BY_HAND, Database, LookupCache, Migration
from .models import Contact, Ping, TeamChannel

MIGRATIONS = [
    Migration(
        "0001_contacts_and_pings",
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
    """,
    ),
    Migration(
        "0002_looked_up_contacts",
        """
    -- Slack ids looked up through Slack's API, too. A CHECK can't be changed in place.
    CREATE TABLE slack_contacts_new (  -- kept: where a person's pings go
        login TEXT PRIMARY KEY,
        slack_id TEXT,                 -- NULL: looked up, and nobody has their email
        source TEXT NOT NULL CHECK (source IN ('import', 'dashboard', 'lookup'))
    );
    INSERT INTO slack_contacts_new SELECT login, slack_id, source FROM slack_contacts;
    DROP TABLE slack_contacts;
    ALTER TABLE slack_contacts_new RENAME TO slack_contacts;
    """,
    ),
    Migration(
        "0003_contact_looked_up_at",
        """
    -- When a lookup ran, so one that found nobody is tried again later. Misses from before
    -- have none, and are tried again at the next prune.
    ALTER TABLE slack_contacts ADD COLUMN looked_up_at TEXT;
    """,
    ),
    Migration(
        "0004_team_channels_by_hand",
        """
    -- A team's channel is set by hand (an import sets it once, as if by hand), so it needs no
    -- source: imported channels are kept, as set by hand.
    CREATE TABLE slack_team_channels_new ( -- kept: where a team's pings go, set by hand
        team TEXT PRIMARY KEY,
        channel_name TEXT,
        channel_id TEXT
    );
    INSERT INTO slack_team_channels_new
        SELECT team, channel_name, channel_id FROM slack_team_channels;
    DROP TABLE slack_team_channels;
    ALTER TABLE slack_team_channels_new RENAME TO slack_team_channels;
    """,
    ),
]
"""The slack plugin's migrations. Every table is kept: only the dashboard knows these."""


class SlackRepository:
    """People's and teams' Slack destinations, and the pings sent. A person's Slack id and a
    ping say where they came from (`source`): the dashboard (`BY_HAND`: set by hand, or a ping
    marked as sent), a lookup through Slack's API by email (`LOOKED_UP`, a Slack id's), or
    `mfd import`, which this version no longer has (`IMPORTED`); people's Slack ids are an
    `api.LookupCache`. A team's channel is always set by hand."""

    def __init__(self, db: Database):
        self._db = db
        self._contacts = LookupCache(db, "slack_contacts", "login", "slack_id")

    def contacts(self) -> dict[str, Contact]:
        return {
            login: Contact(login=login, slack_id=slack_id)
            for login, slack_id in self._contacts.values().items()
        }

    def team_channels(self) -> dict[str, TeamChannel]:
        return {
            row["team"]: TeamChannel(
                slug=row["team"], channel_name=row["channel_name"], channel_id=row["channel_id"]
            )
            for row in self._db.query("SELECT * FROM slack_team_channels")
        }

    def sources(self) -> dict[str, str]:
        """Where each contact (`user:<login>`) and team channel (`team:<team>`) came from."""
        teams = self._db.query("SELECT team FROM slack_team_channels")
        return {f"user:{login}": source for login, source in self._contacts.sources().items()} | {
            f"team:{row['team']}": BY_HAND for row in teams
        }

    def save_looked_up(self, login: str, slack_id: str | None, now: datetime) -> None:
        """Remember what a lookup found, or that it found nobody, unless the login is known."""
        self._contacts.save_looked_up(login, slack_id, now)

    def forget_misses_before(self, cutoff: datetime) -> int:
        """Forget lookups that found nobody before `cutoff`, so they're tried again (the
        person may have joined Slack since); how many."""
        return self._contacts.forget_misses_before(cutoff)

    def set_contact(self, login: str, slack_id: str | None) -> None:
        """Set a person's Slack id by hand; it wins over a lookup from now on."""
        self._contacts.set_by_hand(login, slack_id)

    def unset_contact(self, login: str) -> bool:
        """Forget a person's Slack id; False if there was none. A lookup can bring it back."""
        return self._contacts.forget(login)

    def set_team_channel(self, team: str, name: str | None, channel_id: str | None) -> None:
        """Set a team's channel by hand, in place of the one set before."""
        self._db.execute(
            "INSERT OR REPLACE INTO slack_team_channels VALUES (?, ?, ?)", (team, name, channel_id)
        )

    def unset_team_channel(self, team: str) -> bool:
        """Forget a team's channel; False if there was none."""
        with self._db.transaction() as conn:
            return (
                conn.execute("DELETE FROM slack_team_channels WHERE team = ?", (team,)).rowcount > 0
            )

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
                BY_HAND,
            ),
        )

    def delete_pings_before(self, cutoff: datetime) -> int:
        """Delete pings sent before `cutoff`; returns how many."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM slack_pings WHERE sent_at < ?", (cutoff.isoformat(),)
            ).rowcount
