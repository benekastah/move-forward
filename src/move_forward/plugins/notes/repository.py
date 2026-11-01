"""The notes plugin's tables, and the queries over them."""

from collections import defaultdict
from datetime import date

from ...db import Database
from ...graph import Ref
from .models import Declined, Entry, Ice, IcedItem, Link, Section, Size, Todo

MIGRATIONS = [
    """
    CREATE TABLE notes_entries (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL UNIQUE,
        status TEXT,
        location TEXT,                 -- "Where", as written
        blocked_on TEXT,
        updated_on TEXT
    );
    CREATE TABLE notes_entry_lines (   -- lines the notes don't model, kept as written
        entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
        position INTEGER NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (entry_id, position)
    );
    CREATE TABLE notes_links (         -- what an entry is about
        entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
        kind TEXT NOT NULL,            -- "pr", "ticket"
        ref TEXT NOT NULL,             -- a PR URL, a ticket key
        PRIMARY KEY (entry_id, kind, ref)
    );
    CREATE TABLE notes_todos (
        id INTEGER PRIMARY KEY,
        entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
        position INTEGER NOT NULL,
        text TEXT NOT NULL,
        size TEXT CHECK (size IN ('quick', 'big')),
        done_on TEXT                   -- NULL while open
    );
    CREATE TABLE notes_todo_links (    -- what one todo is about
        todo_id INTEGER NOT NULL REFERENCES notes_todos ON DELETE CASCADE,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        PRIMARY KEY (todo_id, kind, ref)
    );
    CREATE TABLE notes_conditions (    -- merge conditions
        entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
        position INTEGER NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (entry_id, position)
    );
    CREATE TABLE notes_declined (      -- offered and turned down for the day
        entry_id INTEGER NOT NULL REFERENCES notes_entries ON DELETE CASCADE,
        position INTEGER NOT NULL,
        on_date TEXT NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (entry_id, position)
    );
    CREATE TABLE notes_sections (      -- parts of the notes that aren't entries, as written
        position INTEGER PRIMARY KEY,
        heading TEXT,                  -- NULL for the text before the first heading
        body TEXT NOT NULL
    );
    CREATE TABLE notes_ice (           -- one act of icing
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        iced_on TEXT NOT NULL,
        note TEXT
    );
    CREATE TABLE notes_iced (          -- what one act of icing covers
        ice_id INTEGER NOT NULL REFERENCES notes_ice ON DELETE CASCADE,
        position INTEGER NOT NULL,     -- a stack's order, bottom first
        kind TEXT NOT NULL,            -- "branch"
        ref_id TEXT NOT NULL,          -- "<repo dir>\\t<branch>"
        sha TEXT,
        PRIMARY KEY (kind, ref_id)
    );
    """,
    """
    CREATE TABLE notes_ice_covers (    -- derived: what else an act of icing hides, e.g. its
        ice_id INTEGER NOT NULL        -- branches' PRs; replaced after every refresh
            REFERENCES notes_ice ON DELETE CASCADE,
        kind TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        PRIMARY KEY (ice_id, kind, ref_id)
    );
    """,
    # Names, so notes needn't parse git's branch ids for them; branches iced so far were all
    # `<repo dir>\t<name>`.
    """
    ALTER TABLE notes_iced ADD COLUMN name TEXT NOT NULL DEFAULT '';
    ALTER TABLE notes_iced ADD COLUMN repo_dir TEXT;
    UPDATE notes_iced
    SET repo_dir = substr(ref_id, 1, instr(ref_id, char(9)) - 1),
        name = substr(ref_id, instr(ref_id, char(9)) + 1)
    WHERE kind = 'branch' AND instr(ref_id, char(9)) > 0;
    UPDATE notes_iced SET name = ref_id WHERE name = '';
    """,
]
"""The notes plugin's migrations. Every table but the derived `notes_ice_covers` is kept:
only the notes know what's in them."""


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class NotesRepository:
    """Entries with their todos, icing, and the notes' other sections."""

    def __init__(self, db: Database):
        self._db = db

    # Reading

    def entries(self) -> list[Entry]:
        """Every entry, in the order they were added, each with everything it holds."""
        db = self._db
        links: defaultdict[int, list[Link]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_links ORDER BY entry_id, kind, ref"):
            links[row["entry_id"]].append(Link(kind=row["kind"], ref=row["ref"]))
        todo_links: defaultdict[int, list[Link]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_todo_links ORDER BY todo_id, kind, ref"):
            todo_links[row["todo_id"]].append(Link(kind=row["kind"], ref=row["ref"]))
        todos: defaultdict[int, list[Todo]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_todos ORDER BY entry_id, position"):
            todos[row["entry_id"]].append(
                Todo(
                    id=row["id"],
                    text=row["text"],
                    size=row["size"],
                    done_on=_date(row["done_on"]),
                    links=todo_links[row["id"]],
                )
            )
        conditions: defaultdict[int, list[str]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_conditions ORDER BY entry_id, position"):
            conditions[row["entry_id"]].append(row["text"])
        declined: defaultdict[int, list[Declined]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_declined ORDER BY entry_id, position"):
            declined[row["entry_id"]].append(
                Declined(on=date.fromisoformat(row["on_date"]), text=row["text"])
            )
        lines: defaultdict[int, list[str]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_entry_lines ORDER BY entry_id, position"):
            lines[row["entry_id"]].append(row["text"])
        return [
            Entry(
                id=row["id"],
                title=row["title"],
                status=row["status"],
                where=row["location"],
                blocked_on=row["blocked_on"],
                updated_on=_date(row["updated_on"]),
                todos=todos[row["id"]],
                conditions=conditions[row["id"]],
                declined=declined[row["id"]],
                lines=lines[row["id"]],
                links=links[row["id"]],
            )
            for row in db.query("SELECT * FROM notes_entries ORDER BY id")
        ]

    def entry_id(self, title: str) -> int | None:
        rows = self._db.query("SELECT id FROM notes_entries WHERE title = ?", (title,))
        return rows[0]["id"] if rows else None

    def ices(self) -> list[Ice]:
        """Every act of icing, oldest first, with what it covers in order."""
        items: defaultdict[int, list[IcedItem]] = defaultdict(list)
        for row in self._db.query("SELECT * FROM notes_iced ORDER BY ice_id, position"):
            items[row["ice_id"]].append(
                IcedItem(
                    kind=row["kind"],
                    ref_id=row["ref_id"],
                    name=row["name"],
                    repo_dir=row["repo_dir"],
                    sha=row["sha"],
                )
            )
        return [
            Ice(
                id=row["id"],
                title=row["title"],
                iced_on=date.fromisoformat(row["iced_on"]),
                note=row["note"],
                items=items[row["id"]],
            )
            for row in self._db.query("SELECT * FROM notes_ice ORDER BY id")
        ]

    def covers(self) -> dict[int, list[Ref]]:
        """What else each act of icing hides, by its id, as `cover` last found it."""
        found: defaultdict[int, list[Ref]] = defaultdict(list)
        for row in self._db.query("SELECT * FROM notes_ice_covers ORDER BY ice_id, kind, ref_id"):
            found[row["ice_id"]].append(Ref(kind=row["kind"], id=row["ref_id"]))
        return dict(found)

    def sections(self) -> list[Section]:
        return [
            Section(heading=row["heading"], body=row["body"])
            for row in self._db.query("SELECT * FROM notes_sections ORDER BY position")
        ]

    def is_empty(self) -> bool:
        return not self._db.query(
            "SELECT 1 FROM notes_entries UNION ALL SELECT 1 FROM notes_ice"
            " UNION ALL SELECT 1 FROM notes_sections LIMIT 1"
        )

    # Writing

    def add_entry(
        self,
        title: str,
        updated_on: date,
        status: str | None = None,
        where: str | None = None,
        links: list[Link] | None = None,
    ) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO notes_entries (title, status, location, updated_on)"
                " VALUES (?, ?, ?, ?)",
                (title, status, where, updated_on.isoformat()),
            )
            entry_id = cursor.lastrowid
            assert entry_id is not None
            conn.executemany(
                "INSERT OR IGNORE INTO notes_links VALUES (?, ?, ?)",
                [(entry_id, link.kind, link.ref) for link in links or []],
            )
        return entry_id

    def set_status(self, entry_id: int, status: str | None) -> None:
        self._db.execute("UPDATE notes_entries SET status = ? WHERE id = ?", (status, entry_id))

    def set_where(self, entry_id: int, where: str | None) -> None:
        self._db.execute("UPDATE notes_entries SET location = ? WHERE id = ?", (where, entry_id))

    def set_blocked_on(self, entry_id: int, blocked_on: str | None) -> None:
        self._db.execute(
            "UPDATE notes_entries SET blocked_on = ? WHERE id = ?", (blocked_on, entry_id)
        )

    def link_entry(self, entry_id: int, link: Link) -> bool:
        """Say the entry is about `link`; False if it already was."""
        with self._db.transaction() as conn:
            return (
                conn.execute(
                    "INSERT OR IGNORE INTO notes_links VALUES (?, ?, ?)",
                    (entry_id, link.kind, link.ref),
                ).rowcount
                > 0
            )

    def add_condition(self, entry_id: int, text: str) -> bool:
        """Add a merge condition at the end; False if the entry already has it."""
        with self._db.transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM notes_conditions WHERE entry_id = ? AND text = ?", (entry_id, text)
            ).fetchone():
                return False
            conn.execute(
                "INSERT INTO notes_conditions VALUES (?,"
                " (SELECT COALESCE(MAX(position), -1) + 1 FROM notes_conditions"
                " WHERE entry_id = ?), ?)",
                (entry_id, entry_id, text),
            )
        return True

    def remove_condition(self, entry_id: int, text: str) -> bool:
        """Drop a merge condition; False if the entry didn't have it."""
        with self._db.transaction() as conn:
            return (
                conn.execute(
                    "DELETE FROM notes_conditions WHERE entry_id = ? AND text = ?",
                    (entry_id, text),
                ).rowcount
                > 0
            )

    def add_declined(self, entry_id: int, on: date, text: str) -> None:
        """Record that `text` was offered for the entry and turned down on `on`."""
        self._db.execute(
            "INSERT INTO notes_declined VALUES (?,"
            " (SELECT COALESCE(MAX(position), -1) + 1 FROM notes_declined WHERE entry_id = ?),"
            " ?, ?)",
            (entry_id, entry_id, on.isoformat(), text),
        )

    def delete_entry(self, entry_id: int) -> None:
        """Forget an entry, with its todos and everything else it holds."""
        self._db.execute("DELETE FROM notes_entries WHERE id = ?", (entry_id,))

    def touch(self, entry_id: int, on: date) -> None:
        """Record that the entry changed on `on`."""
        self._db.execute(
            "UPDATE notes_entries SET updated_on = ? WHERE id = ?", (on.isoformat(), entry_id)
        )

    def add_todo(
        self, entry_id: int, text: str, size: Size | None = None, links: list[Link] | None = None
    ) -> int:
        """Add an open todo at the end of the entry's list."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO notes_todos (entry_id, position, text, size) VALUES (?,"
                " (SELECT COALESCE(MAX(position), -1) + 1 FROM notes_todos WHERE entry_id = ?),"
                " ?, ?)",
                (entry_id, entry_id, text, size),
            )
            todo_id = cursor.lastrowid
            assert todo_id is not None
            conn.executemany(
                "INSERT OR IGNORE INTO notes_todo_links VALUES (?, ?, ?)",
                [(todo_id, link.kind, link.ref) for link in links or []],
            )
        return todo_id

    def set_done(self, todo_id: int, on: date | None) -> None:
        """Mark a todo done on `on`, or open again with None."""
        self._db.execute(
            "UPDATE notes_todos SET done_on = ? WHERE id = ?",
            (on.isoformat() if on else None, todo_id),
        )

    def delete_todo(self, todo_id: int) -> None:
        self._db.execute("DELETE FROM notes_todos WHERE id = ?", (todo_id,))

    def link_todo(self, todo_id: int, link: Link) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO notes_todo_links VALUES (?, ?, ?)",
            (todo_id, link.kind, link.ref),
        )

    def add_ice(self, title: str, iced_on: date, note: str | None, items: list[IcedItem]) -> int:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO notes_ice (title, iced_on, note) VALUES (?, ?, ?)",
                (title, iced_on.isoformat(), note),
            )
            ice_id = cursor.lastrowid
            assert ice_id is not None
            conn.executemany(
                "INSERT INTO notes_iced (ice_id, position, kind, ref_id, sha, name, repo_dir)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (ice_id, i, item.kind, item.ref_id, item.sha, item.name, item.repo_dir)
                    for i, item in enumerate(items)
                ],
            )
        return ice_id

    def replace_covers(self, ice_ids: list[int], covers: dict[int, list[Ref]]) -> None:
        """Replace what the acts of icing `ice_ids` hide besides their own items. Others' are
        left alone, e.g. a stack iced while a refresh found covers, and an act thawed meanwhile
        stays gone."""
        rows = [(i, r.kind, r.id, i) for i in ice_ids for r in covers.get(i, [])]
        with self._db.transaction() as conn:
            conn.executemany(
                "DELETE FROM notes_ice_covers WHERE ice_id = ?", [(i,) for i in ice_ids]
            )
            conn.executemany(
                "INSERT OR IGNORE INTO notes_ice_covers"
                " SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM notes_ice WHERE id = ?)",
                rows,
            )

    def delete_ice(self, ice_id: int) -> None:
        """Thaw: forget the act of icing and what it covered."""
        self._db.execute("DELETE FROM notes_ice WHERE id = ?", (ice_id,))

    def replace_all(
        self,
        entries: list[Entry],
        ices: list[Ice],
        sections: list[Section],
    ) -> None:
        """Replace everything with what an import read, in one transaction. The given ids are
        ignored; rows get new ones."""
        with self._db.transaction() as conn:
            for table in ("notes_entries", "notes_ice", "notes_sections"):
                conn.execute(f"DELETE FROM {table}")
            for entry in entries:
                cursor = conn.execute(
                    "INSERT INTO notes_entries (title, status, location, blocked_on, updated_on)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        entry.title,
                        entry.status,
                        entry.where,
                        entry.blocked_on,
                        entry.updated_on.isoformat() if entry.updated_on else None,
                    ),
                )
                entry_id = cursor.lastrowid
                conn.executemany(
                    "INSERT OR IGNORE INTO notes_links VALUES (?, ?, ?)",
                    [(entry_id, link.kind, link.ref) for link in entry.links],
                )
                conn.executemany(
                    "INSERT INTO notes_entry_lines VALUES (?, ?, ?)",
                    [(entry_id, i, line) for i, line in enumerate(entry.lines)],
                )
                conn.executemany(
                    "INSERT INTO notes_conditions VALUES (?, ?, ?)",
                    [(entry_id, i, text) for i, text in enumerate(entry.conditions)],
                )
                conn.executemany(
                    "INSERT INTO notes_declined VALUES (?, ?, ?, ?)",
                    [(entry_id, i, d.on.isoformat(), d.text) for i, d in enumerate(entry.declined)],
                )
                for position, todo in enumerate(entry.todos):
                    cursor = conn.execute(
                        "INSERT INTO notes_todos (entry_id, position, text, size, done_on)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (
                            entry_id,
                            position,
                            todo.text,
                            todo.size,
                            todo.done_on.isoformat() if todo.done_on else None,
                        ),
                    )
                    conn.executemany(
                        "INSERT OR IGNORE INTO notes_todo_links VALUES (?, ?, ?)",
                        [(cursor.lastrowid, link.kind, link.ref) for link in todo.links],
                    )
            for ice in ices:
                cursor = conn.execute(
                    "INSERT INTO notes_ice (title, iced_on, note) VALUES (?, ?, ?)",
                    (ice.title, ice.iced_on.isoformat(), ice.note),
                )
                # A branch in two stacks stays with the first: something is on ice once.
                conn.executemany(
                    "INSERT OR IGNORE INTO notes_iced"
                    " (ice_id, position, kind, ref_id, sha, name, repo_dir)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            cursor.lastrowid,
                            i,
                            item.kind,
                            item.ref_id,
                            item.sha,
                            item.name,
                            item.repo_dir,
                        )
                        for i, item in enumerate(ice.items)
                    ],
                )
            conn.executemany(
                "INSERT INTO notes_sections VALUES (?, ?, ?)",
                [(i, s.heading, s.body) for i, s in enumerate(sections)],
            )
