"""The git_stack plugin's table: the branches git-stack tracks, as `git stack info` last said."""

from collections import defaultdict

from ...db import Database
from ...domain import GitStackSnapshot, StackBranch, StackRepo

MIGRATIONS = [
    """
    CREATE TABLE git_stack_branches (  -- fetched: `git stack info` in each checkout
        repo_dir TEXT NOT NULL,
        position INTEGER NOT NULL,     -- git stack info's order
        name TEXT NOT NULL,
        parent TEXT,                   -- NULL for a root, e.g. main
        status TEXT,
        current INTEGER NOT NULL,
        stack_group TEXT,
        PRIMARY KEY (repo_dir, name)
    );
    -- What the last refresh kept as JSON in core's snapshots, so the page has it at once.
    INSERT OR IGNORE INTO git_stack_branches
        SELECT json_extract(repo.value, '$.dir'), branch.key,
            json_extract(branch.value, '$.name'), json_extract(branch.value, '$.parent'),
            json_extract(branch.value, '$.status'),
            COALESCE(json_extract(branch.value, '$.current'), 0),
            json_extract(branch.value, '$.group')
        FROM snapshots,
            json_each(snapshots.payload, '$.repos') AS repo,
            json_each(repo.value, '$.branches') AS branch
        WHERE snapshots.source = 'git_stack' AND snapshots.payload IS NOT NULL;
    """
]
"""The git_stack plugin's migrations."""


class StackRepository:
    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: GitStackSnapshot) -> None:
        """Keep what `git stack info` said, in place of what it said last time."""
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM git_stack_branches")
            conn.executemany(
                "INSERT INTO git_stack_branches VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (repo.dir, i, b.name, b.parent, b.status, b.current, b.group)
                    for repo in snapshot.repos
                    for i, b in enumerate(repo.branches)
                ],
            )

    def load(self) -> GitStackSnapshot:
        branches: defaultdict[str, list[StackBranch]] = defaultdict(list)
        for row in self._db.query("SELECT * FROM git_stack_branches ORDER BY repo_dir, position"):
            branches[row["repo_dir"]].append(
                StackBranch(
                    name=row["name"],
                    parent=row["parent"],
                    status=row["status"],
                    current=bool(row["current"]),
                    group=row["stack_group"],
                )
            )
        return GitStackSnapshot(
            repos=[StackRepo(dir=d, branches=found) for d, found in branches.items()]
        )
