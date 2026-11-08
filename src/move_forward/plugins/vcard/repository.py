"""The vcard plugin's tables: its migrations, and keeping and reading what it fetched."""

from ...api import Database, Migration
from .models import Contact, VcardSnapshot

MIGRATIONS = [
    Migration(
        "0001_people",
        """
    CREATE TABLE vcard_people ( -- fetched: everyone in the address book with an address
        email TEXT PRIMARY KEY,
        position INTEGER NOT NULL,
        name TEXT NOT NULL
    );
    CREATE TABLE vcard_person_groups ( -- fetched: the groups each is in
        email TEXT NOT NULL,
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (email, name)
    );
    """,
    ),
]


class ContactRepository:
    """`vcard_people` and their `vcard_person_groups`: replaced wholesale by each refresh."""

    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: VcardSnapshot) -> None:
        """Keep `snapshot` in place of what was there, all at once."""
        people = [(c.email, position, c.name) for position, c in enumerate(snapshot.contacts)]
        groups = [
            (c.email, position, name)
            for c in snapshot.contacts
            for position, name in enumerate(c.groups)
        ]
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM vcard_people")
            conn.execute("DELETE FROM vcard_person_groups")
            conn.executemany("INSERT INTO vcard_people VALUES (?, ?, ?)", people)
            conn.executemany("INSERT INTO vcard_person_groups VALUES (?, ?, ?)", groups)

    def load(self) -> VcardSnapshot:
        groups: dict[str, list[str]] = {}
        for row in self._db.query(
            "SELECT email, name FROM vcard_person_groups ORDER BY email, position"
        ):
            groups.setdefault(row["email"], []).append(row["name"])
        return VcardSnapshot(
            contacts=[
                Contact(email=row["email"], name=row["name"], groups=groups.get(row["email"], []))
                for row in self._db.query("SELECT email, name FROM vcard_people ORDER BY position")
            ]
        )
