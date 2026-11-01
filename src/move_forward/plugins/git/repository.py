"""The git plugin's tables: each checkout, its worktrees and branches, and the directories I
commit to, as the last refresh found them."""

from collections import defaultdict
from datetime import datetime

from ...db import Database
from .models import Branch, LocalRepo, LocalSnapshot, Worktree

_FROM_SNAPSHOT = "FROM snapshots, json_each(snapshots.payload, '$.repos') AS repo"
_GIT_SNAPSHOT = "snapshots.source = 'git' AND snapshots.payload IS NOT NULL"

MIGRATIONS = [
    f"""
    CREATE TABLE git_repos (           -- fetched: the checkouts in [git] repos
        dir TEXT PRIMARY KEY,
        position INTEGER NOT NULL,     -- [git] repos' order
        github_repo TEXT,
        default_branch TEXT NOT NULL
    );
    CREATE TABLE git_worktrees (       -- fetched
        repo_dir TEXT NOT NULL REFERENCES git_repos ON DELETE CASCADE,
        position INTEGER NOT NULL,
        path TEXT NOT NULL,
        branch TEXT,                   -- NULL when detached
        modified INTEGER NOT NULL,
        untracked INTEGER NOT NULL,
        PRIMARY KEY (repo_dir, position)
    );
    CREATE TABLE git_branches (        -- fetched
        repo_dir TEXT NOT NULL REFERENCES git_repos ON DELETE CASCADE,
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        sha TEXT,
        upstream TEXT,
        ahead INTEGER NOT NULL,
        behind INTEGER NOT NULL,
        gone INTEGER NOT NULL,
        committed_at TEXT NOT NULL,
        ahead_of_default INTEGER,
        PRIMARY KEY (repo_dir, position)
    );
    CREATE TABLE git_my_dirs (         -- fetched: where my recent commits went
        repo_dir TEXT NOT NULL REFERENCES git_repos ON DELETE CASCADE,
        path TEXT NOT NULL,
        PRIMARY KEY (repo_dir, path)
    );
    -- What the last refresh kept as JSON in core's snapshots, so the page has it at once.
    INSERT OR IGNORE INTO git_repos
        SELECT json_extract(repo.value, '$.dir'), repo.key,
            json_extract(repo.value, '$.github_repo'), json_extract(repo.value, '$.default_branch')
        {_FROM_SNAPSHOT} WHERE {_GIT_SNAPSHOT};
    INSERT OR IGNORE INTO git_worktrees
        SELECT json_extract(repo.value, '$.dir'), wt.key, json_extract(wt.value, '$.path'),
            json_extract(wt.value, '$.branch'), json_extract(wt.value, '$.modified'),
            json_extract(wt.value, '$.untracked')
        {_FROM_SNAPSHOT}, json_each(repo.value, '$.worktrees') AS wt WHERE {_GIT_SNAPSHOT};
    INSERT OR IGNORE INTO git_branches
        SELECT json_extract(repo.value, '$.dir'), b.key, json_extract(b.value, '$.name'),
            json_extract(b.value, '$.sha'), json_extract(b.value, '$.upstream'),
            COALESCE(json_extract(b.value, '$.ahead'), 0),
            COALESCE(json_extract(b.value, '$.behind'), 0),
            COALESCE(json_extract(b.value, '$.gone'), 0),
            json_extract(b.value, '$.committed_at'), json_extract(b.value, '$.ahead_of_default')
        {_FROM_SNAPSHOT}, json_each(repo.value, '$.branches') AS b WHERE {_GIT_SNAPSHOT};
    INSERT OR IGNORE INTO git_my_dirs
        SELECT json_extract(repo.value, '$.dir'), d.value
        {_FROM_SNAPSHOT}, json_each(repo.value, '$.my_dirs') AS d WHERE {_GIT_SNAPSHOT};
    """
]
"""The git plugin's migrations."""


class LocalRepository:
    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: LocalSnapshot) -> None:
        """Keep what a refresh found, in place of the last refresh's."""
        repos = snapshot.repos
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM git_repos")  # and, by cascade, everything in them
            conn.executemany(
                "INSERT INTO git_repos VALUES (?, ?, ?, ?)",
                [(r.dir, n, r.github_repo, r.default_branch) for n, r in enumerate(repos)],
            )
            conn.executemany(
                "INSERT INTO git_worktrees VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (r.dir, n, w.path, w.branch, w.modified, w.untracked)
                    for r in repos
                    for n, w in enumerate(r.worktrees)
                ],
            )
            conn.executemany(
                "INSERT INTO git_branches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.dir,
                        n,
                        b.name,
                        b.sha,
                        b.upstream,
                        b.ahead,
                        b.behind,
                        b.gone,
                        b.committed_at.isoformat(),
                        b.ahead_of_default,
                    )
                    for r in repos
                    for n, b in enumerate(r.branches)
                ],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO git_my_dirs VALUES (?, ?)",
                [(r.dir, d) for r in repos for d in r.my_dirs],
            )

    def load(self) -> LocalSnapshot:
        db = self._db
        worktrees: defaultdict[str, list[Worktree]] = defaultdict(list)
        for row in db.query("SELECT * FROM git_worktrees ORDER BY repo_dir, position"):
            worktrees[row["repo_dir"]].append(
                Worktree(
                    path=row["path"],
                    branch=row["branch"],
                    modified=row["modified"],
                    untracked=row["untracked"],
                )
            )
        branches: defaultdict[str, list[Branch]] = defaultdict(list)
        for row in db.query("SELECT * FROM git_branches ORDER BY repo_dir, position"):
            branches[row["repo_dir"]].append(
                Branch(
                    name=row["name"],
                    sha=row["sha"],
                    upstream=row["upstream"],
                    ahead=row["ahead"],
                    behind=row["behind"],
                    gone=bool(row["gone"]),
                    committed_at=datetime.fromisoformat(row["committed_at"]),
                    ahead_of_default=row["ahead_of_default"],
                )
            )
        my_dirs: defaultdict[str, list[str]] = defaultdict(list)
        for row in db.query("SELECT * FROM git_my_dirs ORDER BY repo_dir, path"):
            my_dirs[row["repo_dir"]].append(row["path"])
        return LocalSnapshot(
            repos=[
                LocalRepo(
                    dir=row["dir"],
                    github_repo=row["github_repo"],
                    default_branch=row["default_branch"],
                    worktrees=worktrees[row["dir"]],
                    branches=branches[row["dir"]],
                    my_dirs=my_dirs[row["dir"]],
                )
                for row in db.query("SELECT * FROM git_repos ORDER BY position")
            ]
        )
