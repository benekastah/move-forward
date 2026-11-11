"""A plugin's kept cache of values looked up by key (a login's email, a login's Slack id),
which a hand can also set.

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

What's set by hand wins over a lookup, which never overwrites anything, and one that found nothing is tried again once `forget_misses_before` lets it go.
"""

import re
from datetime import datetime
from typing import Literal

from .db import Database

Source = Literal["dashboard", "import", "lookup"]
BY_HAND: Source = "dashboard"
"""Set by hand (on the dashboard or with `mfd`): wins over the rest."""
IMPORTED: Source = "import"
"""Read by `mfd import`, which this version no longer has: such a value stays, until it's set
by hand or forgotten, and wins over a lookup."""
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
        """Set a key's value by hand; it wins over lookups from now on."""
        self._db.execute(
            f"INSERT OR REPLACE INTO {self._table} ({self._key}, {self._value}, source)"
            " VALUES (?, ?, ?)",
            (key, value, BY_HAND),
        )

    def forget(self, key: str) -> bool:
        """Forget a key's value, however it was set; False if there was none. A lookup can
        bring it back."""
        with self._db.transaction() as conn:
            return (
                conn.execute(f"DELETE FROM {self._table} WHERE {self._key} = ?", (key,)).rowcount
                > 0
            )
