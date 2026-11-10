"""The SQLite connection and migrations. Queries live in `repositories`.

Core and every plugin own a list of migrations, each named, applied in order: SQL scripts,
or Python for a data fix SQL can't say. Which have run is kept by name in the
`migrations` ledger, so a migration runs once per database, and new ones are appended,
never edited, renamed or reordered.
"""

import functools
import logging
import os
import re
import sqlite3
import sys
import threading
from collections.abc import Callable, Generator, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .private import make_private

log = logging.getLogger(__name__)

CORE = "core"
"""The owner of core's own migrations."""


@dataclass(frozen=True)
class Migration:
    """One step of an owner's tables, kept by `name` once it has run."""

    name: str
    """What the ledger keeps it as, e.g. `0002_projects`: never renamed once shipped."""
    run: str | Callable[[sqlite3.Connection], None]
    """SQL to run, or a function given the connection, inside the migration's
    transaction (it mustn't commit)."""


OWNER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
"""What a migrations owner may be called; owner names are written into SQL."""
BACKUPS_DIR = "backups"
"""Next to the database: copies taken before migrations, `<db name>.<UTC time>`."""
BACKUP_TIME_FORMAT = "%Y%m%dT%H%M%S.%fZ"
"""Sorts by time as text, so the oldest backups sort first."""
DEFAULT_KEEP_BACKUPS = 10
SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")
"""Files SQLite keeps beside a database; they belong to it, they aren't backups of their own."""
BUSY_TIMEOUT_SECONDS = 10
"""How long a write waits for another process's (e.g. `mfd notes` while the server runs)."""

V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    source TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    payload TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS work_items (
    position INTEGER PRIMARY KEY,
    item TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iced_stacks (
    bottom_branch TEXT NOT NULL,
    repo_dir TEXT NOT NULL,
    stack TEXT NOT NULL,
    PRIMARY KEY (bottom_branch, repo_dir)
);
CREATE TABLE IF NOT EXISTS reviewer_users (
    login TEXT PRIMARY KEY,
    email TEXT,
    slack_id TEXT,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_channels (
    slug TEXT PRIMARY KEY,
    channel_name TEXT,
    channel_id TEXT,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pings (
    id INTEGER PRIMARY KEY,
    pr_url TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS pings_pr_url ON pings (pr_url);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
"""The schema before migrations. `IF NOT EXISTS` lets databases made before them upgrade.

Core no longer uses `reviewer_users`, `team_channels` or `pings`, but keeps them: the slack
and github plugins' first migrations copy from them, whenever those plugins are enabled,
and core's migrations run first, so emptying them would lose what's there."""

_V2_KV_AND_LAYOUT = """
CREATE TABLE kv (
    plugin TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (plugin, key)
);
INSERT INTO kv SELECT 'core', key, value FROM meta;
DROP TABLE meta;
CREATE TABLE layout (
    kind TEXT NOT NULL CHECK (kind IN ('group', 'panel')),
    key TEXT NOT NULL,
    parent TEXT,
    position INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

_V3_PLUGIN_SOURCES = """
UPDATE snapshots SET source = 'git' WHERE source = 'local';
UPDATE snapshots SET source = 'github.derived' WHERE source = 'derived';
"""
"""Snapshots are named after the plugin that stores them."""

_V4_NOTES_IN_THE_NOTES_PLUGIN = """
DROP TABLE work_items;
DROP TABLE iced_stacks;
"""
"""Core's copies of the markdown notes; the notes plugin's own tables hold them now."""

_V5_ACT_NOW_FEEDBACK = """
CREATE TABLE act_now_feedback (        -- kept: what I said of an Act now item; pruned
    subject_kind TEXT NOT NULL,        -- after prune_days, snoozes once they're over
    subject_id TEXT NOT NULL,
    verb TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('snooze', 'dismiss', 'pin')),
    until TEXT,                        -- for a snooze
    fingerprint TEXT NOT NULL,         -- the item's when it was snoozed or dismissed
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, verb)
);
"""
"""Snoozing, dismissing and pinning Act now's items."""

_V6_NO_SNAPSHOT_JSON = """
UPDATE snapshots SET payload = NULL WHERE payload IS NOT NULL;
"""
"""Each plugin keeps what it fetched in its own tables now, and core no longer writes the
JSON copy; what's left is the last one before that. It held only fetched data, which the
next refresh brings back, so a plugin enabled later loses nothing it needs. The column
stays: plugins' first migrations, which copied from it, still name it."""

_V7_PRIORITIES = """
CREATE TABLE priorities (              -- kept: how much I said an entity matters
    kind TEXT NOT NULL,                -- until it's done (see `refresh.forget_finished_priorities`)
    id TEXT NOT NULL,
    level INTEGER NOT NULL CHECK (level BETWEEN 0 AND 2),
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, id)
);
"""
"""Priorities: 0 not important, 1 important, 2 critical, by entity."""

_V8_PENDING_FEEDBACK = """
CREATE TABLE act_now_feedback_new (    -- as before, and `pending`: acted on elsewhere, hidden
    subject_kind TEXT NOT NULL,        -- until its plugin has fetched again (until: at most)
    subject_id TEXT NOT NULL,
    verb TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('snooze', 'dismiss', 'pin', 'pending')),
    until TEXT,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, verb)
);
INSERT INTO act_now_feedback_new SELECT * FROM act_now_feedback;
DROP TABLE act_now_feedback;
ALTER TABLE act_now_feedback_new RENAME TO act_now_feedback;
"""
"""Lets Act now's feedback say an item was acted on elsewhere."""

_V9_NOTIFICATIONS = """
CREATE TABLE notifications (           -- kept for `keep_hours`: events waiting for a page
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- to tell me of them
    kind TEXT NOT NULL,                -- `<plugin>.<kind>`
    thing TEXT NOT NULL,               -- what it happened to; one notification per thing
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    anchor TEXT NOT NULL,              -- the item's id on the page, or ''
    created_at TEXT NOT NULL,
    told_at TEXT
);
"""
"""Browser notifications' events, until a dashboard tab has shown them."""

_V10_SESSIONS = """
CREATE TABLE sessions (                -- kept until they expire or are revoked: browsers
    id_hash TEXT PRIMARY KEY,          -- signed in; the id's SHA-256, so the table can't sign in
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""
"""Browser sessions: what a signed-in browser's cookie names, instead of the token itself."""

_V11_KINDS_NAMED_FOR_PLUGINS = """
UPDATE priorities SET kind = CASE kind
    WHEN 'pr' THEN 'pull_request'
    WHEN 'branch' THEN 'git.branch'
    WHEN 'repo' THEN 'git.repo'
    WHEN 'stack_branch' THEN 'git_stack.stack_branch'
    WHEN 'entry' THEN 'notes.entry'
    WHEN 'todo' THEN 'notes.todo'
    WHEN 'iced' THEN 'notes.iced'
    ELSE kind
END;
UPDATE act_now_feedback SET subject_kind = CASE subject_kind
    WHEN 'pr' THEN 'pull_request'
    WHEN 'branch' THEN 'git.branch'
    WHEN 'repo' THEN 'git.repo'
    WHEN 'stack_branch' THEN 'git_stack.stack_branch'
    WHEN 'entry' THEN 'notes.entry'
    WHEN 'todo' THEN 'notes.todo'
    WHEN 'iced' THEN 'notes.iced'
    ELSE subject_kind
END;
"""
"""Kinds a plugin publishes are named for it (`git.branch`), and pull requests' family is
`pull_request`. No row can collide: no kind had a dot before, and `pull_request` is new."""

_V12_FEEDBACK_BY_TASK = """
CREATE TABLE act_now_feedback_new (    -- kept: what I said of an Act now item; pruned
    subject_kind TEXT NOT NULL,        -- after prune_days, snoozes once they're over
    subject_id TEXT NOT NULL,
    task TEXT NOT NULL,                -- the item's `Signals.task`, e.g. review
    action TEXT NOT NULL,              -- snooze, dismiss, pin or pending
    until TEXT,                        -- for a snooze, or a pending hide
    fingerprint TEXT NOT NULL,         -- the item's when it was set aside
    created_at TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, task)
);
INSERT OR IGNORE INTO act_now_feedback_new
SELECT subject_kind, subject_id,
    CASE
        WHEN verb LIKE 'Move to %' OR verb = 'Update the status' THEN 'transition'
        WHEN verb IN ('Bump the review', 'Ping the reviewers') THEN 'ping'
        WHEN verb LIKE 'Finish % quick todos' THEN 'close_out'
        ELSE lower(replace(trim(verb), ' ', '_'))
    END,
    action, until, fingerprint, created_at
FROM act_now_feedback
WHERE trim(verb) != '';
DROP TABLE act_now_feedback;
ALTER TABLE act_now_feedback_new RENAME TO act_now_feedback;
"""
"""Act now keeps what I said of an item by its task, a stable id, rather than its verb, the
words it shows: rewording a verb, or a count in it changing, dropped the item's snooze.
Each verb becomes its task (`Review` is `review`), the ones that held a status or a count
their plugin's task; an item without a verb took no feedback. `action` loses its CHECK, so a
new kind of feedback needs no rebuild."""

_V13_OPENED = """
UPDATE act_now_feedback SET action = 'opened' WHERE action = 'pending';
"""
"""An item I acted on elsewhere is dimmed until it changes (`opened`), rather than hidden
until its plugin fetches again (`pending`)."""

_V14_CONTACTS = """
CREATE TABLE contacts (                -- kept: what I said people are to me
    who TEXT PRIMARY KEY,              -- an address, or @domain for everyone there
    contact_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""
"""Contact types I set: customer, partner and so on (see `people`)."""

_V15_TAGS = """
CREATE TABLE tags (                    -- kept: the #tags I put on entities, until I take them off
                                       -- or it's gone, as a deleted entry is (see `tags.forget_gone`)
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    tag TEXT NOT NULL,                 -- its handle, e.g. garbage-day
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, id, tag)
);
"""
"""Tags I set on entities, besides those in their text (see `tags`)."""

_V16_NO_UNIMPORTANT_PRIORITY = """
DELETE FROM priorities WHERE level = 0;
"""
"""A priority is important (1) or critical (2): "not important" (0) is gone, and Snooze,
Dismiss or ice set work aside instead."""

_V17_DISMISS_IS_A_SNOOZE = """
UPDATE act_now_feedback SET action = 'snooze', until = NULL WHERE action = 'dismiss';
"""
"""A dismissal was a snooze with no end: it's kept as one now, a snooze until the item
changes (`until` NULL)."""

_V18_SHOWN_NOTICES = """
CREATE TABLE shown_notices (           -- derived, replaced after each refresh: the items with
    plugin TEXT NOT NULL,              -- a notification (`Item.notify`) the plugin's panels
    kind TEXT NOT NULL,                -- showed, by the plugin's kind (`branch_deletable`)
    thing TEXT NOT NULL,               -- and what it's about
    PRIMARY KEY (plugin, kind, thing)
);
CREATE TABLE shown_plugins (           -- derived: the plugins whose panels shown_notices
    plugin TEXT PRIMARY KEY            -- holds, empty or not
);
"""
"""What the plugins' panels showed at the last refresh, so the next tells what's newly shown,
after a restart too."""

_V19_PROBLEMS = """
CREATE TABLE problems (                -- kept: what a plugin reports wrong that isn't a failed
                                       -- refresh, until it says it's gone (`Context.problem`),
                                       -- or prune_days after it last said it
    plugin TEXT NOT NULL,
    key TEXT NOT NULL,                 -- the plugin's name for it, e.g. email_lookups
    text TEXT NOT NULL,                -- what's wrong, as the page shows it
    seen_at TEXT NOT NULL,             -- when the plugin last reported it, changed or not
    PRIMARY KEY (plugin, key)
);
"""
"""Problems the plugins report besides a failed refresh, kept across restarts so they're
shown, and logged, once rather than again after each."""

CORE_MIGRATIONS = [
    Migration("0001_schema", V1_SCHEMA),
    Migration("0002_kv_and_layout", _V2_KV_AND_LAYOUT),
    Migration("0003_plugin_sources", _V3_PLUGIN_SOURCES),
    Migration("0004_notes_in_the_notes_plugin", _V4_NOTES_IN_THE_NOTES_PLUGIN),
    Migration("0005_act_now_feedback", _V5_ACT_NOW_FEEDBACK),
    Migration("0006_no_snapshot_json", _V6_NO_SNAPSHOT_JSON),
    Migration("0007_priorities", _V7_PRIORITIES),
    Migration("0008_pending_feedback", _V8_PENDING_FEEDBACK),
    Migration("0009_notifications", _V9_NOTIFICATIONS),
    Migration("0010_sessions", _V10_SESSIONS),
    Migration("0011_kinds_named_for_plugins", _V11_KINDS_NAMED_FOR_PLUGINS),
    Migration("0012_feedback_by_task", _V12_FEEDBACK_BY_TASK),
    Migration("0013_opened", _V13_OPENED),
    Migration("0014_contacts", _V14_CONTACTS),
    Migration("0015_tags", _V15_TAGS),
    Migration("0016_no_unimportant_priority", _V16_NO_UNIMPORTANT_PRIORITY),
    Migration("0017_dismiss_is_a_snooze", _V17_DISMISS_IS_A_SNOOZE),
    Migration("0018_shown_notices", _V18_SHOWN_NOTICES),
    Migration("0019_problems", _V19_PROBLEMS),
]

_SCHEMA_VERSIONS = """
CREATE TABLE IF NOT EXISTS schema_versions (
    plugin TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS migrations (  -- the ledger: which migrations ran, by name
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (owner, name)
);
"""
"""What every database has before any migration runs: the ledger of migrations by name,
and `schema_versions`, how many of each owner's ran, from before the ledger (kept up to
date for a version of the dashboard from before it)."""

Params = tuple[object, ...]


def _private(path: Path) -> None:
    """Make `path` a file only I can read, creating it empty if it isn't there: the notes and
    what the plugins fetched are mine, not other users' of this machine. On Unix SQLite gives
    its -wal and -shm files the database's permissions; on Windows they'd take their folder's,
    so `Database` makes them first (see `_side_files`)."""
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    make_private(path)


def _side_files(path: Path) -> list[Path]:
    """The -wal and -shm files SQLite keeps beside the database in WAL mode."""
    return [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]


class Database:
    """One SQLite connection, shared between threads under a lock."""

    def __init__(self, path: Path | str, keep_backups: int = DEFAULT_KEEP_BACKUPS):
        """`keep_backups`: how many copies taken before migrations to keep; 0 takes none."""
        self._path = None if path == ":memory:" else Path(path)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            _private(self._path)
            if sys.platform == "win32":  # SQLite opens them as they are, keeping their access
                for side in _side_files(self._path):
                    _private(side)
        self._keep_backups = keep_backups
        self._backed_up = not (self._path and self._path.exists() and self._path.stat().st_size)
        """Whether there's no need for a backup: one was taken, or there's nothing to lose."""
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=BUSY_TIMEOUT_SECONDS
        )
        self._conn.row_factory = sqlite3.Row
        # Foreign keys are off in SQLite unless asked for, and `ON DELETE CASCADE` needs them.
        # (A migration that rebuilds a table someone references must say so; see `migrate`.)
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path:
            # Readers don't block the writer, so other processes can use it while we do.
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        """Held by whoever uses the connection; re-entered by `reading`'s holder's queries."""
        with self._lock:
            self._conn.executescript(_SCHEMA_VERSIONS)
        self.migrate(CORE, CORE_MIGRATIONS)

    def migrate(
        self, owner: str, migrations: Sequence[Migration], owners: Iterable[str] = ()
    ) -> int:
        """Apply `owner`'s migrations that haven't run yet, in order, each in its own
        transaction with its line in the ledger. A plugin's (any owner but core's) may make or
        remove only tables, indexes, views and triggers named `<owner>_...`, but not those of
        another of `owners` whose name starts so (`git_stack_...` for `git`); one that touches
        another is rolled back and refused. Another process (`mfd notes` while `mfd serve`
        starts) running the same migration first is fine: it's taken as done.

        Foreign keys are enforced, and SQLite can't turn them off inside a transaction, so a
        migration that rebuilds a referenced table copies it under a new name and renames,
        rather than dropping the original first.

        Returns how many it ran (not those another process did). Fails, running nothing, when
        the ledger's names aren't the first of `migrations`, in order: one was renamed or
        reordered, or the database was used by a newer version of the dashboard.
        """
        if not OWNER_RE.match(owner):
            raise ValueError(f"bad migrations owner {owner!r}")
        names = [m.name for m in migrations]
        if duplicates := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"{owner} has two migrations named {', '.join(duplicates)}")
        with self._lock:
            applied = self._applied(owner, names)
            if applied[: len(names)] != names[: len(applied)]:
                raise RuntimeError(
                    f"{owner}'s migrations were renamed or reordered: the database ran"
                    f" {', '.join(applied)}, in that order"
                )
            if len(applied) > len(names):
                raise RuntimeError(
                    f"the database has run {owner} migrations this version doesn't know"
                    f" ({', '.join(applied[len(names) :])}); upgrade the dashboard"
                )
            pending = migrations[len(applied) :]
            if pending:
                self._backup()
            longer = [o for o in owners if o != owner and o.startswith(f"{owner}_")]
            ran = 0
            for version, migration in enumerate(pending, start=len(applied) + 1):
                try:
                    self._run(owner, version, migration, longer)
                    ran += 1
                except Exception:
                    if migration.name in self._applied(owner, names):
                        continue  # another process ran it meanwhile
                    raise
            return ran

    def _applied(self, owner: str, names: list[str]) -> list[str]:
        """The names of `owner`'s migrations the database has run, in order. A database from
        before the ledger counted them in `schema_versions`: those are the first of `names`,
        written to the ledger now. Call with the lock held."""
        rows = self._conn.execute(
            "SELECT name FROM migrations WHERE owner = ? ORDER BY rowid", (owner,)
        ).fetchall()
        if rows:
            return [row["name"] for row in rows]
        row = self._conn.execute(
            "SELECT version FROM schema_versions WHERE plugin = ?", (owner,)
        ).fetchone()
        counted: int = row["version"] if row else 0
        if counted > len(names):
            raise RuntimeError(
                f"the database has {counted} {owner} migrations but this version knows"
                f" {len(names)}; upgrade the dashboard"
            )
        if counted:
            now = datetime.now(UTC).isoformat()
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO migrations VALUES (?, ?, ?)",
                    [(owner, name, now) for name in names[:counted]],
                )
        return names[:counted]

    def _run(self, owner: str, version: int, migration: Migration, others: list[str]) -> None:
        """Run one migration and record it, in one transaction. Call with the lock held."""
        conn = self._conn
        before = self._objects()
        try:
            if isinstance(migration.run, str):
                # executescript commits any open transaction first, then runs the script,
                # which opens one of its own and leaves it open for the checks and records.
                conn.executescript(f"BEGIN;\n{migration.run}")
            else:
                conn.execute("BEGIN")
                migration.run(conn)
            if owner != CORE:
                after = self._objects()
                changed = {"made": after - before, "removed": before - after}
                for did, names in changed.items():
                    if stray := sorted(
                        n
                        for n in names
                        if not n.startswith(f"{owner}_")
                        or any(n.startswith(f"{other}_") for other in others)
                    ):
                        raise ValueError(
                            f"{owner}'s migration {migration.name} {did} {', '.join(stray)}:"
                            f" a plugin's tables are named {owner}_..., and only its own"
                        )
            conn.execute(
                "INSERT INTO migrations VALUES (?, ?, ?)",
                (owner, migration.name, datetime.now(UTC).isoformat()),
            )
            conn.execute("INSERT OR REPLACE INTO schema_versions VALUES (?, ?)", (owner, version))
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    def _objects(self) -> set[str]:
        """The names of the database's tables, indexes, views and triggers, but SQLite's own."""
        return {
            row["name"]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
            )
        }

    def _backup(self) -> None:
        """Copy the database into `BACKUPS_DIR` before its first migration in this process,
        then delete all but the newest `keep_backups` copies. Call with the lock held."""
        if self._backed_up or self._path is None or self._keep_backups <= 0:
            return
        self._backed_up = True
        backups = self._path.parent / BACKUPS_DIR
        backups.mkdir(mode=0o700, exist_ok=True)
        make_private(backups)
        stamp = datetime.now(UTC).strftime(BACKUP_TIME_FORMAT)
        target = backups / f"{self._path.name}.{stamp}"
        _private(target)
        copy = sqlite3.connect(target)
        try:
            self._conn.backup(copy)
            # The copy inherits WAL mode; a rollback journal keeps it one self-contained file
            # (no -wal and -shm beside it once something opens it), which is what a backup is.
            (mode,) = copy.execute("PRAGMA journal_mode = DELETE").fetchone()
        finally:
            copy.close()
        if mode == "delete":  # everything is in the file now; SQLite can leave an empty -shm
            for suffix in SQLITE_SIDE_FILES:
                target.with_name(target.name + suffix).unlink(missing_ok=True)
        log.info("backed up %s to %s before migrating", self._path, target)
        copies = sorted(
            path
            for path in backups.glob(f"{self._path.name}.*")
            if not path.name.endswith(SQLITE_SIDE_FILES)
        )
        for path in copies[: -self._keep_backups]:
            path.unlink()
            for suffix in SQLITE_SIDE_FILES:
                path.with_name(path.name + suffix).unlink(missing_ok=True)

    @contextmanager
    def reading(self) -> Generator[None]:
        """Several reads, with no write of another thread between them: a plugin's `load`
        reads its tables in several queries, and mustn't get half of one refresh's and half of
        the next's."""
        with self._lock:
            yield

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """The connection, locked, in a transaction that commits on success. Not within
        another: the lock lets its thread in again, and SQLite would commit the outer one's
        writes with the inner's."""
        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError("a transaction within a transaction")
            with self._conn:
                yield self._conn

    def query(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        with self.transaction() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: Params = ()) -> None:
        with self.transaction() as conn:
            conn.execute(sql, params)


@functools.cache
def core_objects() -> frozenset[str]:
    """The tables, indexes, views and triggers core makes, as a fresh database has them."""
    db = Database(":memory:")
    try:
        with db._lock:  # pyright: ignore[reportPrivateUsage]
            return frozenset(db._objects())  # pyright: ignore[reportPrivateUsage]
    finally:
        db._conn.close()  # pyright: ignore[reportPrivateUsage]
