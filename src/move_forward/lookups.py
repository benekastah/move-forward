"""A plugin's kept cache of values looked up by key (a login's email, a login's Slack id),
which an import or a hand can also set.

The plugin owns the table and its migrations; `LookupCache` owns what's done with it. The
table has a text primary key, a nullable value (NULL: looked up, and none found), a `source`
(those of `Source` the plugin uses) and a `looked_up_at` time:

```sql
CREATE TABLE <plugin>_<things> (    -- kept: ...
    <key> TEXT PRIMARY KEY,
    <value> TEXT,
    source TEXT NOT NULL CHECK (source IN ('dashboard', 'import', 'lookup')),
    looked_up_at TEXT
);
```

What's set by hand wins over an import, which wins over a lookup; a lookup never overwrites
anything, and one that found nothing is tried again once `forget_misses_before` lets it go.
"""

import re
import sqlite3
from datetime import datetime
from typing import Literal

from .db import Database

Source = Literal["dashboard", "import", "lookup"]
BY_HAND: Source = "dashboard"
"""Set by hand (on the dashboard or with `mfd`): wins over the rest, and an import leaves it."""
IMPORTED: Source = "import"
"""Read by an import, which replaces what the last one read; wins over a lookup."""
LOOKED_UP: Source = "lookup"
"""Found by a lookup; never overwrites anything else."""
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


class LookupCache:
    """The values in a plugin's lookup table (see the module's docstring), by key."""

    def __init__(self, db: Database, table: str, key: str, value: str):
        """Over `table`, keyed by its column `key`, holding its column `value`."""
        for name in (table, key, value):
            if not _IDENTIFIER.match(name):
                raise ValueError(f"{name!r} isn't a table or column name")
        self._db = db
        self._table = table
        self._key = key
        self._value = value

    def values(self) -> dict[str, str | None]:
        """Every key's value; None where a lookup found none."""
        rows = self._db.query(f"SELECT {self._key}, {self._value} FROM {self._table}")
        return {row[0]: row[1] for row in rows}

    def sources(self) -> dict[str, Source]:
        """Where each key's value came from."""
        rows = self._db.query(f"SELECT {self._key}, source FROM {self._table}")
        return {row[0]: row[1] for row in rows}

    def save_looked_up(self, key: str, value: str | None, now: datetime) -> None:
        """Remember what a lookup found, or that it found nothing (None), unless the key is
        known already."""
        self._db.execute(
            f"INSERT OR IGNORE INTO {self._table} ({self._key}, {self._value}, source,"
            " looked_up_at) VALUES (?, ?, ?, ?)",
            (key, value, LOOKED_UP, now.isoformat()),
        )

    def forget_misses_before(self, cutoff: datetime) -> int:
        """Forget lookups that found nothing before `cutoff`, so they're tried again; how
        many. What a lookup found stays."""
        with self._db.transaction() as conn:
            return conn.execute(
                f"DELETE FROM {self._table} WHERE source = ? AND {self._value} IS NULL"
                " AND (looked_up_at IS NULL OR looked_up_at < ?)",
                (LOOKED_UP, cutoff.isoformat()),
            ).rowcount

    def set_by_hand(self, key: str, value: str | None) -> None:
        """Set a key's value by hand; it wins over imports and lookups from now on."""
        self._db.execute(
            f"INSERT OR REPLACE INTO {self._table} ({self._key}, {self._value}, source)"
            " VALUES (?, ?, ?)",
            (key, value, BY_HAND),
        )

    def forget(self, key: str) -> bool:
        """Forget a key's value, however it was set; False if there was none. An import or a
        lookup can bring it back."""
        with self._db.transaction() as conn:
            return (
                conn.execute(f"DELETE FROM {self._table} WHERE {self._key} = ?", (key,)).rowcount
                > 0
            )

    def replace_imported(
        self, values: dict[str, str], conn: sqlite3.Connection | None = None
    ) -> None:
        """Replace what the last import read with `values`, over a lookup of the same key but
        not over what's set by hand. Within `conn`'s transaction, when given (from
        `Database.transaction`), so it's one with the caller's other writes."""
        if conn is None:
            with self._db.transaction() as own:
                self.replace_imported(values, own)
            return
        conn.execute(f"DELETE FROM {self._table} WHERE source = ?", (IMPORTED,))
        conn.executemany(
            f"INSERT INTO {self._table} ({self._key}, {self._value}, source) VALUES (?, ?, ?)"
            f" ON CONFLICT ({self._key}) DO UPDATE"
            f" SET {self._value} = excluded.{self._value}, source = excluded.source,"
            " looked_up_at = NULL"
            f" WHERE {self._table}.source = ?",
            [(key, value, IMPORTED, LOOKED_UP) for key, value in values.items()],
        )
