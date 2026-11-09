"""The notes plugin's tables, and the queries over them."""

import sqlite3
from collections import defaultdict
from collections.abc import Callable, Collection
from datetime import date, datetime

from ...api import Database, Migration, Ref, localtime
from .markdown import BRANCH, attach_conditions
from .models import Condition, Entry, Ice, IcedItem, Link, Section, Size, Todo


def _attach_old_conditions(conn: sqlite3.Connection) -> None:
    """Give each condition kept before conditions named their PR its PR, by
    `markdown.attach_conditions`: the PR its text names, else each of its entry's PRs; one
    with neither becomes a line of its entry."""
    old = conn.execute(
        "SELECT entry_id, text FROM notes_conditions WHERE pr IS NULL ORDER BY entry_id, position"
    ).fetchall()
    texts: defaultdict[int, list[str]] = defaultdict(list)
    for row in old:
        texts[row["entry_id"]].append(row["text"])
    for entry_id, written in texts.items():
        prs = [
            row["ref"]
            for row in conn.execute(
                # The kind as 0007 named it, whatever the live constant becomes.
                "SELECT ref FROM notes_links WHERE entry_id = ? AND kind = 'pull_request'"
                " ORDER BY ref",
                (entry_id,),
            )
        ]
        conditions, lines = attach_conditions(written, prs)
        conn.execute("DELETE FROM notes_conditions WHERE entry_id = ? AND pr IS NULL", (entry_id,))
        for condition in conditions:
            _insert_condition(conn, entry_id, condition)
        for line in lines:
            conn.execute(
                "INSERT INTO notes_entry_lines VALUES (?,"
                " (SELECT COALESCE(MAX(position), -1) + 1 FROM notes_entry_lines"
                " WHERE entry_id = ?), ?)",
                (entry_id, entry_id, line),
            )


def _remember_bookkeeping(conn: sqlite3.Connection) -> None:
    """Copy what the skill's bookkeeping commands kept (`decline`, `checked`, `asked-done`,
    `offered`, `asked`) into `notes_remembered`, under the keys `mfd notes remember` takes for
    them: the command, then its arguments, joined by `:`, an entry by its title. An offer's
    time is kept as its local day. The latest day wins where two rows make one key."""
    found: list[tuple[str, str]] = [
        (f"decline:{row['title']}:{row['text']}", row["on_date"])
        for row in conn.execute(
            "SELECT title, text, MAX(on_date) AS on_date FROM notes_declined"
            " JOIN notes_entries ON notes_entries.id = entry_id GROUP BY entry_id, text"
        )
    ]
    for command, column in (("checked", "checked_on"), ("asked-done", "asked_done_on")):
        found += [
            (f"{command}:{row['title']}", row[column])
            for row in conn.execute(
                f"SELECT title, {column} FROM notes_entries WHERE {column} IS NOT NULL"
            )
        ]
    found += [
        (
            f"offered:{row['pr']}:{row['kind']}",
            localtime.day(datetime.fromisoformat(row["offered_at"])).isoformat(),
        )
        for row in conn.execute("SELECT pr, kind, offered_at FROM notes_offers")
    ]
    # `mfd notes asked` kept its days in core's kv, as the notes' `last_asked_<question>`.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'kv'").fetchone():
        asked = "plugin = 'notes' AND key LIKE 'last!_asked!_%' ESCAPE '!'"
        found += [
            (f"asked:{row['key'].removeprefix('last_asked_')}", row["value"])
            for row in conn.execute(f"SELECT key, value FROM kv WHERE {asked}")
            if row["value"]
        ]
        conn.execute(f"DELETE FROM kv WHERE {asked}")
    conn.executemany(
        "INSERT INTO notes_remembered VALUES (?, ?)"
        " ON CONFLICT (key) DO UPDATE SET on_date = MAX(on_date, excluded.on_date)",
        found,
    )


MIGRATIONS = [
    Migration(
        "0001_notes",
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
    ),
    Migration(
        "0002_ice_covers",
        """
    CREATE TABLE notes_ice_covers (    -- derived: what else an act of icing hides, e.g. its
        ice_id INTEGER NOT NULL        -- branches' PRs; replaced after every refresh
            REFERENCES notes_ice ON DELETE CASCADE,
        kind TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        PRIMARY KEY (ice_id, kind, ref_id)
    );
    """,
    ),
    # Names, so notes needn't parse git's branch ids for them; branches iced so far were all
    # `<repo dir>\t<name>`.
    Migration(
        "0003_iced_names",
        """
    ALTER TABLE notes_iced ADD COLUMN name TEXT NOT NULL DEFAULT '';
    ALTER TABLE notes_iced ADD COLUMN repo_dir TEXT;
    UPDATE notes_iced
    SET repo_dir = substr(ref_id, 1, instr(ref_id, char(9)) - 1),
        name = substr(ref_id, instr(ref_id, char(9)) + 1)
    WHERE kind = 'branch' AND instr(ref_id, char(9)) > 0;
    UPDATE notes_iced SET name = ref_id WHERE name = '';
    """,
    ),
    Migration(
        "0004_check_ins",
        """
    ALTER TABLE notes_entries ADD COLUMN checked_on TEXT;     -- the skill last checked in
    ALTER TABLE notes_entries ADD COLUMN asked_done_on TEXT;  -- it last asked if it's done
    UPDATE notes_entries SET
        checked_on = (SELECT MAX(on_date) FROM notes_declined
                      WHERE entry_id = notes_entries.id AND text = 'Checked'),
        asked_done_on = (SELECT MAX(on_date) FROM notes_declined
                         WHERE entry_id = notes_entries.id AND text = 'Asked-done');
    DELETE FROM notes_declined WHERE text IN ('Checked', 'Asked-done');
    """,
    ),
    Migration(
        "0005_offers",
        """
    CREATE TABLE notes_offers (        -- kept: what the skill offered about a PR, and when;
        pr TEXT NOT NULL,              -- pruned after prune_days
        kind TEXT NOT NULL CHECK (kind IN ('nudge', 'fresh-review')),
        offered_at TEXT NOT NULL,      -- the last time, UTC
        PRIMARY KEY (pr, kind)
    );
    """,
    ),
    Migration(
        "0006_condition_prs",
        """
    ALTER TABLE notes_conditions ADD COLUMN pr TEXT;  -- the PR it holds; NULL for one from
                                                      -- before, until it's given its PR
    """,
    ),
    # Kinds named for the plugins that publish them, and pull requests' family `pull_request`.
    Migration(
        "0007_kinds_named_for_plugins",
        """
    UPDATE notes_links SET kind = CASE kind
        WHEN 'pr' THEN 'pull_request'
        WHEN 'branch' THEN 'git.branch'
        WHEN 'entry' THEN 'notes.entry'
        ELSE kind
    END;
    UPDATE notes_todo_links SET kind = CASE kind
        WHEN 'pr' THEN 'pull_request'
        WHEN 'branch' THEN 'git.branch'
        WHEN 'entry' THEN 'notes.entry'
        ELSE kind
    END;
    UPDATE notes_iced SET kind = CASE kind
        WHEN 'pr' THEN 'pull_request'
        WHEN 'branch' THEN 'git.branch'
        WHEN 'entry' THEN 'notes.entry'
        ELSE kind
    END;
    UPDATE notes_ice_covers SET kind = CASE kind
        WHEN 'pr' THEN 'pull_request'
        WHEN 'branch' THEN 'git.branch'
        WHEN 'entry' THEN 'notes.entry'
        ELSE kind
    END;
    """,
    ),
    # Conditions from before conditions named their PR are given theirs, once.
    Migration("0008_old_conditions_given_prs", _attach_old_conditions),
    Migration(
        "0009_remembered",
        """
    CREATE TABLE notes_remembered (    -- kept: the day something the skill remembers last
        key TEXT PRIMARY KEY,          -- happened, by its key (`mfd notes remember`); pruned
        on_date TEXT NOT NULL          -- after prune_days
    );
    """,
    ),
    Migration("0010_bookkeeping_remembered", _remember_bookkeeping),
    # What 0010 copied into notes_remembered. Entries' two columns stay, emptied: dropping a
    # column needs SQLite 3.35, newer than some systems' Python has.
    Migration(
        "0011_drop_bookkeeping",
        """
    DROP TABLE notes_declined;
    DROP TABLE notes_offers;
    UPDATE notes_entries SET checked_on = NULL, asked_done_on = NULL;
    """,
    ),
]
"""The notes plugin's migrations. Every table but the derived `notes_ice_covers` is kept:
only the notes know what's in them."""


def _insert_condition(conn: sqlite3.Connection, entry_id: int, condition: Condition) -> None:
    conn.execute(
        "INSERT INTO notes_conditions (entry_id, position, text, pr) VALUES (?,"
        " (SELECT COALESCE(MAX(position), -1) + 1 FROM notes_conditions WHERE entry_id = ?),"
        " ?, ?)",
        (entry_id, entry_id, condition.text, condition.pr),
    )


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


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
        conditions: defaultdict[int, list[Condition]] = defaultdict(list)
        for row in db.query("SELECT * FROM notes_conditions ORDER BY entry_id, position"):
            conditions[row["entry_id"]].append(Condition(pr=row["pr"], text=row["text"]))
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

    def remember(self, key: str, on: date) -> None:
        """Record that what `key` names happened on `on`, replacing the day before."""
        self._db.execute(
            "INSERT OR REPLACE INTO notes_remembered VALUES (?, ?)", (key, on.isoformat())
        )

    def recall(self, key: str) -> date | None:
        """The day `remember` last recorded for `key`; None when it never did (or the prune
        forgot it)."""
        rows = self._db.query("SELECT on_date FROM notes_remembered WHERE key = ?", (key,))
        return date.fromisoformat(rows[0]["on_date"]) if rows else None

    def forget_remembered_by(self, day: date) -> int:
        """Forget what was remembered as happening on or before `day`; how many went."""
        with self._db.transaction() as conn:
            return conn.execute(
                "DELETE FROM notes_remembered WHERE on_date <= ?", (day.isoformat(),)
            ).rowcount

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

    def add_condition(self, entry_id: int, condition: Condition) -> bool:
        """Add a merge condition at the end; False if the entry already has it."""
        with self._db.transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM notes_conditions WHERE entry_id = ? AND pr = ? AND text = ?",
                (entry_id, condition.pr, condition.text),
            ).fetchone():
                return False
            _insert_condition(conn, entry_id, condition)
        return True

    def remove_condition(self, entry_id: int, text: str, pr: str | None = None) -> bool:
        """Drop a merge condition, on `pr` or on every PR it's on; False if there was none."""
        query = "DELETE FROM notes_conditions WHERE entry_id = ? AND text = ?"
        params: tuple[object, ...] = (entry_id, text)
        if pr is not None:
            query += " AND pr = ?"
            params += (pr,)
        with self._db.transaction() as conn:
            return conn.execute(query, params).rowcount > 0

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

    def set_todo_size(self, todo_id: int, size: Size) -> None:
        self._db.execute("UPDATE notes_todos SET size = ? WHERE id = ?", (size, todo_id))

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

    def rekey(self, kinds: Collection[str], identify: Callable[[str, str], str]) -> int:
        """Give the links and what's on ice of `kinds` the ids `identify` says their entities
        have now (`Graph.identify`: a ticket kept by its key gets its URL); where the new
        id is there already, the old row goes, and an act of icing it leaves with nothing.
        How many ids changed. `identify` runs before
        the transaction, not in it: it asks plugins (`url_for`), which may use the database."""
        marks = ", ".join("?" * len(kinds))
        tables = [
            ("notes_links", "ref"),
            ("notes_todo_links", "ref"),
            ("notes_iced", "ref_id"),
        ]
        moves = [
            (table, column, row["kind"], row["id"], new)
            for table, column in tables
            for row in self._db.query(
                f"SELECT DISTINCT kind, {column} AS id FROM {table} WHERE kind IN ({marks})",
                (*kinds,),
            )
            if (new := identify(row["kind"], row["id"])) != row["id"]
        ]
        with self._db.transaction() as conn:
            touched: set[int] = set()  # the acts of icing whose items moved
            for table, column, kind, old, new in moves:
                if table == "notes_iced":
                    touched |= {
                        row["ice_id"]
                        for row in conn.execute(
                            "SELECT ice_id FROM notes_iced WHERE kind = ? AND ref_id = ?",
                            (kind, old),
                        )
                    }
                conn.execute(
                    f"UPDATE OR IGNORE {table} SET {column} = ? WHERE kind = ? AND {column} = ?",
                    (new, kind, old),
                )
                conn.execute(f"DELETE FROM {table} WHERE kind = ? AND {column} = ?", (kind, old))
            # One whose every item was on ice already, in another, is that one; an act with no
            # items of its own (a stack imported without branches) is left alone.
            conn.executemany(
                "DELETE FROM notes_ice WHERE id = ?"
                " AND NOT EXISTS (SELECT 1 FROM notes_iced WHERE ice_id = notes_ice.id)",
                [(i,) for i in touched],
            )
        return len(moves)

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

    def set_ice_note(self, ice_id: int, note: str) -> str | None:
        """Say why it's on ice, in place of what was said. Its title; None if it isn't on ice."""
        # The write first: it opens the transaction, so a thaw from another process can't land
        # between it and the read.
        with self._db.transaction() as conn:
            update = "UPDATE notes_ice SET note = ? WHERE id = ?"
            if conn.execute(update, (note, ice_id)).rowcount == 0:
                return None
            row = conn.execute("SELECT title FROM notes_ice WHERE id = ?", (ice_id,)).fetchone()
            return row["title"]

    def delete_ice(self, ice_id: int) -> None:
        """Thaw: forget the act of icing and what it covered."""
        self._db.execute("DELETE FROM notes_ice WHERE id = ?", (ice_id,))

    def replace_all(
        self,
        entries: list[Entry],
        ices: list[Ice],
        sections: list[Section],
    ) -> None:
        """Replace everything with what an import read, in one transaction, but the PRs,
        tickets and entries on ice: `on-ice.md` holds only stacks, so an import can't have
        read them. The given ids are ignored; rows get new ones."""
        with self._db.transaction() as conn:
            for table in ("notes_entries", "notes_sections"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "DELETE FROM notes_ice WHERE NOT EXISTS"
                " (SELECT 1 FROM notes_iced WHERE ice_id = notes_ice.id AND kind != ?)",
                (BRANCH,),
            )
            for entry in entries:
                cursor = conn.execute(
                    "INSERT INTO notes_entries (title, status, location, blocked_on, updated_on)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        entry.title,
                        entry.status,
                        entry.where,
                        entry.blocked_on,
                        _iso(entry.updated_on),
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
                    "INSERT INTO notes_conditions (entry_id, position, text, pr) VALUES (?, ?, ?, ?)",
                    [(entry_id, i, c.text, c.pr) for i, c in enumerate(entry.conditions)],
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
