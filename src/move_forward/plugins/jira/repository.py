"""The jira plugin's tables: my open tickets, as the last refresh found them."""

from collections import defaultdict
from datetime import datetime

from ...db import Database
from ...domain import JiraIssue, JiraSnapshot

MIGRATIONS = [
    """
    CREATE TABLE jira_issues (         -- fetched: my open tickets
        key TEXT PRIMARY KEY,
        position INTEGER NOT NULL,     -- the search's order: most recently updated first
        summary TEXT NOT NULL,
        status TEXT NOT NULL,
        status_category TEXT NOT NULL,
        type TEXT NOT NULL,
        priority TEXT,
        parent TEXT,
        created TEXT NOT NULL,
        updated TEXT NOT NULL,
        resolved TEXT,
        url TEXT NOT NULL
    );
    CREATE TABLE jira_issue_labels (   -- fetched
        key TEXT NOT NULL REFERENCES jira_issues ON DELETE CASCADE,
        position INTEGER NOT NULL,
        label TEXT NOT NULL,
        PRIMARY KEY (key, position)
    );
    CREATE TABLE jira_issue_blockers ( -- fetched: unfinished tickets this one is blocked by
        key TEXT NOT NULL REFERENCES jira_issues ON DELETE CASCADE,
        position INTEGER NOT NULL,
        blocker TEXT NOT NULL,
        PRIMARY KEY (key, position)
    );
    -- What the last refresh kept as JSON in core's snapshots, so the page has it at once.
    INSERT OR IGNORE INTO jira_issues
        SELECT json_extract(issue.value, '$.key'), issue.key,
            json_extract(issue.value, '$.summary'), json_extract(issue.value, '$.status'),
            json_extract(issue.value, '$.status_category'), json_extract(issue.value, '$.type'),
            json_extract(issue.value, '$.priority'), json_extract(issue.value, '$.parent'),
            json_extract(issue.value, '$.created'), json_extract(issue.value, '$.updated'),
            json_extract(issue.value, '$.resolved'), json_extract(issue.value, '$.url')
        FROM snapshots, json_each(snapshots.payload, '$.issues') AS issue
        WHERE snapshots.source = 'jira' AND snapshots.payload IS NOT NULL;
    INSERT OR IGNORE INTO jira_issue_labels
        SELECT json_extract(issue.value, '$.key'), label.key, label.value
        FROM snapshots, json_each(snapshots.payload, '$.issues') AS issue,
            json_each(issue.value, '$.labels') AS label
        WHERE snapshots.source = 'jira' AND snapshots.payload IS NOT NULL;
    INSERT OR IGNORE INTO jira_issue_blockers
        SELECT json_extract(issue.value, '$.key'), blocker.key, blocker.value
        FROM snapshots, json_each(snapshots.payload, '$.issues') AS issue,
            json_each(issue.value, '$.open_blockers') AS blocker
        WHERE snapshots.source = 'jira' AND snapshots.payload IS NOT NULL;
    """
]
"""The jira plugin's migrations."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class IssueRepository:
    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: JiraSnapshot) -> None:
        """Keep the tickets a refresh found, in place of the last refresh's."""
        issues = snapshot.issues
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM jira_issues")  # and, by cascade, their labels and blockers
            conn.executemany(
                "INSERT INTO jira_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        i.key,
                        n,
                        i.summary,
                        i.status,
                        i.status_category,
                        i.type,
                        i.priority,
                        i.parent,
                        i.created.isoformat(),
                        i.updated.isoformat(),
                        _iso(i.resolved),
                        i.url,
                    )
                    for n, i in enumerate(issues)
                ],
            )
            conn.executemany(
                "INSERT INTO jira_issue_labels VALUES (?, ?, ?)",
                [(i.key, n, label) for i in issues for n, label in enumerate(i.labels)],
            )
            conn.executemany(
                "INSERT INTO jira_issue_blockers VALUES (?, ?, ?)",
                [(i.key, n, key) for i in issues for n, key in enumerate(i.open_blockers)],
            )

    def load(self) -> JiraSnapshot:
        labels: defaultdict[str, list[str]] = defaultdict(list)
        for row in self._db.query("SELECT * FROM jira_issue_labels ORDER BY key, position"):
            labels[row["key"]].append(row["label"])
        blockers: defaultdict[str, list[str]] = defaultdict(list)
        for row in self._db.query("SELECT * FROM jira_issue_blockers ORDER BY key, position"):
            blockers[row["key"]].append(row["blocker"])
        return JiraSnapshot(
            issues=[
                JiraIssue(
                    key=row["key"],
                    summary=row["summary"],
                    status=row["status"],
                    status_category=row["status_category"],
                    type=row["type"],
                    labels=labels[row["key"]],
                    priority=row["priority"],
                    created=datetime.fromisoformat(row["created"]),
                    updated=datetime.fromisoformat(row["updated"]),
                    parent=row["parent"],
                    open_blockers=blockers[row["key"]],
                    resolved=datetime.fromisoformat(row["resolved"]) if row["resolved"] else None,
                    url=row["url"],
                )
                for row in self._db.query("SELECT * FROM jira_issues ORDER BY position")
            ]
        )
