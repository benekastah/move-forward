"""The github plugin's table of people: GitHub logins and their commit emails, for mapping
commit authors to reviewers to suggest."""

from typing import Literal

from ...db import Database
from ...markdown import cell, tables

Source = Literal["import", "lookup"]
IMPORTED: Source = "import"
"""From `reviewer-map.md` with `mfd github import`; wins over a lookup."""
LOOKED_UP: Source = "lookup"
"""Found in the person's commits; never overwrites an imported email."""
REVIEWER_MAP_FILE = "reviewer-map.md"

MIGRATIONS = [
    """
    CREATE TABLE github_people (       -- kept: logins and their commit emails
        login TEXT PRIMARY KEY,
        email TEXT,                    -- NULL: looked up, and none found
        source TEXT NOT NULL CHECK (source IN ('import', 'lookup'))
    );
    -- The emails core's reviewer table held for github before it had its own; core keeps the
    -- table, emptied.
    INSERT INTO github_people
        SELECT login, email, CASE source WHEN 'lookup' THEN 'lookup' ELSE 'import' END
        FROM reviewer_users WHERE email IS NOT NULL OR source = 'lookup';
    """
]
"""The github plugin's migrations."""


def read_reviewer_map(text: str) -> dict[str, str]:
    """Emails by login, from the Users table of `reviewer-map.md` (login, email, Slack id)."""
    return {
        login: email
        for row in tables(text).get("Users", [])
        if (login := cell(row, 0)) and (email := cell(row, 1))
    }


class PeopleRepository:
    def __init__(self, db: Database):
        self._db = db

    def emails(self) -> dict[str, str | None]:
        """Every known login's email; None where a lookup found none."""
        return {row["login"]: row["email"] for row in self._db.query("SELECT * FROM github_people")}

    def save_looked_up(self, login: str, email: str | None) -> None:
        """Remember what a lookup found (or that it found nothing), unless the login is known."""
        self._db.execute(
            "INSERT OR IGNORE INTO github_people VALUES (?, ?, ?)", (login, email, LOOKED_UP)
        )

    def replace_imported(self, emails: dict[str, str]) -> None:
        """Replace what an earlier import read with these, over any lookup of the same login."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM github_people WHERE source = ?", (IMPORTED,))
            conn.executemany(
                "INSERT OR REPLACE INTO github_people VALUES (?, ?, ?)",
                [(login, email, IMPORTED) for login, email in emails.items()],
            )
