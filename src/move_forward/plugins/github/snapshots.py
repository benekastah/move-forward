"""What github's PR searches found, in its tables (see `tables`), and back."""

from collections import defaultdict
from datetime import datetime
from sqlite3 import Row
from typing import Literal, cast

from ...api import Database, Params
from .models import (
    Check,
    DerivedSnapshot,
    GitHubSnapshot,
    MergeMethod,
    PullRequest,
    Review,
    ReviewerSuggestion,
    TeamPrScore,
    Thread,
    TimelineEvent,
)

ListName = Literal[
    "mine",
    "direct_requests",
    "reviewed_by_me",
    "team_requests",
    "merged_recent",
    "closed_recent",
    "mentioned",
]
LISTS: tuple[ListName, ...] = (
    "mine",
    "direct_requests",
    "reviewed_by_me",
    "team_requests",
    "merged_recent",
    "closed_recent",
    "mentioned",
)
"""The searches, as `GitHubSnapshot` names them."""
CHILD_TABLES = (
    "github_pr_labels",
    "github_pr_assignees",
    "github_pr_requests",
    "github_pr_reviews",
    "github_pr_threads",
    "github_pr_checks",
    "github_pr_events",
    "github_pr_files",
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


Key = tuple[str, str]
"""A PR as one search found it: (list, url)."""


_METHOD_COLUMNS: tuple[tuple[MergeMethod, str], ...] = (
    ("MERGE", "merge_commit_allowed"),
    ("SQUASH", "squash_merge_allowed"),
    ("REBASE", "rebase_merge_allowed"),
)
"""Each merge method, by the column of `github_prs` saying the PR's repo allows it."""


class PrRepository:
    def __init__(self, db: Database):
        self._db = db

    def replace(self, snapshot: GitHubSnapshot) -> None:
        """Keep what the searches found, in place of what the last refresh's found."""
        rows: dict[str, list[Params]] = defaultdict(list)
        for name in LISTS:
            # A search's pages can overlap: the same PR twice is kept once, at its first place.
            firsts: dict[str, PullRequest] = {}
            for pr in getattr(snapshot, name):
                firsts.setdefault(pr.url, pr)
            for position, pr in enumerate(firsts.values()):
                key = (name, pr.url)
                rows["github_prs"].append(
                    (
                        *key,
                        position,
                        pr.number,
                        pr.title,
                        pr.repo,
                        pr.default_branch,
                        pr.is_draft,
                        pr.state,
                        pr.created_at.isoformat(),
                        pr.updated_at.isoformat(),
                        _iso(pr.merged_at),
                        pr.body,
                        pr.head,
                        pr.head_oid,
                        pr.base,
                        pr.author,
                        pr.size,
                        pr.changed_files,
                        pr.mergeable,
                        pr.merge_state,
                        pr.review_decision,
                        pr.in_merge_queue,
                        pr.rollup_state,
                        _iso(pr.last_commit_at),
                        pr.merge_queue,
                        "MERGE" in pr.merge_methods,
                        "SQUASH" in pr.merge_methods,
                        "REBASE" in pr.merge_methods,
                        pr.default_merge_method,
                        pr.auto_merge_allowed,
                        pr.delete_branch_on_merge,
                        pr.auto_merge,
                    )
                )
                rows["github_pr_labels"] += [(*key, i, v) for i, v in enumerate(pr.labels)]
                rows["github_pr_assignees"] += [(*key, i, v) for i, v in enumerate(pr.assignees)]
                rows["github_pr_requests"] += [
                    (*key, i, "user", v) for i, v in enumerate(pr.requested_users)
                ] + [(*key, i, "team", v) for i, v in enumerate(pr.requested_teams)]
                rows["github_pr_reviews"] += [
                    (*key, i, r.author, r.state, _iso(r.submitted_at))
                    for i, r in enumerate(pr.reviews)
                ]
                rows["github_pr_threads"] += [
                    (
                        *key,
                        i,
                        t.resolved,
                        t.outdated,
                        t.path,
                        t.author,
                        t.body,
                        t.url,
                        _iso(t.created_at),
                        t.comment_count,
                    )
                    for i, t in enumerate(pr.threads)
                ]
                rows["github_pr_checks"] += [
                    (*key, i, c.name, c.state, c.url, c.description)
                    for i, c in enumerate(pr.checks)
                ]
                rows["github_pr_events"] += [
                    (
                        *key,
                        i,
                        e.type,
                        e.at.isoformat(),
                        e.actor,
                        e.reviewer_kind,
                        e.reviewer,
                        e.reason,
                        e.state,
                        e.has_body,
                    )
                    for i, e in enumerate(pr.events)
                ]
                rows["github_pr_files"] += [
                    (*key, i, f, pr.file_lines[i] if i < len(pr.file_lines) else 0)
                    for i, f in enumerate(pr.files)
                ]
        with self._db.transaction() as conn:
            for table in (
                "github_prs",
                "github_account",
                "github_team_members",
                "github_team_memberships",
                "github_membership_teams",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("INSERT INTO github_account VALUES (?)", (snapshot.login,))
            conn.executemany(
                "INSERT OR IGNORE INTO github_team_members VALUES (?)",
                [(login,) for login in snapshot.team_members],
            )
            conn.executemany(
                "INSERT INTO github_membership_teams VALUES (?)",
                [(team,) for team in snapshot.team_memberships],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO github_team_memberships VALUES (?, ?)",
                [
                    (team, login)
                    for team, members in snapshot.team_memberships.items()
                    for login in members
                ],
            )
            for table in ("github_prs", *CHILD_TABLES):
                if rows[table]:
                    marks = ", ".join("?" * len(rows[table][0]))
                    conn.executemany(f"INSERT INTO {table} VALUES ({marks})", rows[table])

    def _children(
        self, table: str, key: Key | None = None
    ) -> defaultdict[Key, list[dict[str, object]]]:
        """A child table's rows by PR; just `key`'s, given one."""
        found: defaultdict[Key, list[dict[str, object]]] = defaultdict(list)
        where = "WHERE list = ? AND url = ? " if key else ""
        query = f"SELECT * FROM {table} {where}ORDER BY list, url, position"
        for row in self._db.query(query, key or ()):
            found[(row["list"], row["url"])].append(dict(row))
        return found

    def _pull_request(
        self, row: Row, found: dict[str, defaultdict[Key, list[dict[str, object]]]]
    ) -> PullRequest:
        """A `github_prs` row with its children, by table."""
        key: Key = (row["list"], row["url"])
        return PullRequest(
            url=row["url"],
            number=row["number"],
            title=row["title"],
            repo=row["repo"],
            default_branch=row["default_branch"],
            is_draft=bool(row["is_draft"]),
            state=row["state"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            merged_at=_when(row["merged_at"]),
            body=row["body"],
            head=row["head"],
            head_oid=row["head_oid"],
            base=row["base"],
            author=row["author"],
            size=row["size"],
            changed_files=row["changed_files"],
            labels=[cast(str, r["label"]) for r in found["github_pr_labels"][key]],
            assignees=[cast(str, r["login"]) for r in found["github_pr_assignees"][key]],
            mergeable=row["mergeable"],
            merge_state=row["merge_state"],
            review_decision=row["review_decision"],
            in_merge_queue=bool(row["in_merge_queue"]),
            merge_queue=bool(row["merge_queue"]),
            merge_methods=[method for method, column in _METHOD_COLUMNS if row[column]],
            default_merge_method=row["default_merge_method"],
            auto_merge_allowed=bool(row["auto_merge_allowed"]),
            delete_branch_on_merge=bool(row["delete_branch_on_merge"]),
            auto_merge=row["auto_merge"],
            requested_users=[
                cast(str, r["name"])
                for r in found["github_pr_requests"][key]
                if r["kind"] == "user"
            ],
            requested_teams=[
                cast(str, r["name"])
                for r in found["github_pr_requests"][key]
                if r["kind"] == "team"
            ],
            reviews=[
                Review(
                    author=cast(str | None, r["author"]),
                    state=cast(str, r["state"]),
                    submitted_at=_when(cast(str | None, r["submitted_at"])),
                )
                for r in found["github_pr_reviews"][key]
            ],
            threads=[
                Thread(
                    resolved=bool(r["resolved"]),
                    outdated=bool(r["outdated"]),
                    path=cast(str | None, r["path"]),
                    author=cast(str | None, r["author"]),
                    body=cast(str, r["body"]),
                    url=cast(str | None, r["comment_url"]),
                    created_at=_when(cast(str | None, r["created_at"])),
                    comment_count=cast(int, r["comment_count"]),
                )
                for r in found["github_pr_threads"][key]
            ],
            rollup_state=row["rollup_state"],
            checks=[
                Check(
                    name=cast(str, r["name"]),
                    state=cast(str, r["state"]),
                    url=cast(str | None, r["check_url"]),
                    description=cast(str | None, r["description"]),
                )
                for r in found["github_pr_checks"][key]
            ],
            last_commit_at=_when(row["last_commit_at"]),
            events=[
                TimelineEvent.model_validate(
                    {
                        "type": r["type"],
                        "at": r["at"],
                        "actor": r["actor"],
                        "reviewer_kind": r["reviewer_kind"],
                        "reviewer": r["reviewer"],
                        "reason": r["reason"],
                        "state": r["state"],
                        "has_body": r["has_body"],
                    }
                )
                for r in found["github_pr_events"][key]
            ],
            files=[cast(str, r["path"]) for r in found["github_pr_files"][key]],
            file_lines=[cast(int, r["lines"]) for r in found["github_pr_files"][key]],
        )

    def my_open_pr(self, url: str) -> PullRequest | None:
        """One of my open PRs as the last refresh found it; None if it isn't one."""
        key: Key = ("mine", url)
        rows = self._db.query("SELECT * FROM github_prs WHERE list = ? AND url = ?", key)
        if not rows:
            return None
        return self._pull_request(rows[0], {t: self._children(t, key) for t in CHILD_TABLES})

    def load(self) -> GitHubSnapshot | None:
        """What the last refresh found; None before one."""
        account = self._db.query("SELECT login FROM github_account")
        if not account:
            return None
        found = {table: self._children(table) for table in CHILD_TABLES}
        lists: dict[str, list[PullRequest]] = {name: [] for name in LISTS}
        for row in self._db.query("SELECT * FROM github_prs ORDER BY list, position"):
            lists[row["list"]].append(self._pull_request(row, found))
        members = [r["login"] for r in self._db.query("SELECT login FROM github_team_members")]
        memberships: dict[str, list[str]] = {
            row["team"]: []
            for row in self._db.query("SELECT team FROM github_membership_teams ORDER BY team")
        }
        for row in self._db.query(
            "SELECT team, login FROM github_team_memberships ORDER BY team, login"
        ):
            memberships.setdefault(row["team"], []).append(row["login"])
        return GitHubSnapshot(
            login=account[0]["login"],
            team_members=members,
            team_memberships=dict(memberships),
            **lists,
        )


class DerivedRepository:
    """Reviewer suggestions and team scores, as `after_collect` last worked them out."""

    def __init__(self, db: Database):
        self._db = db

    def replace(self, derived: DerivedSnapshot) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM github_reviewer_suggestions")
            conn.execute("DELETE FROM github_team_scores")
            conn.executemany(
                "INSERT INTO github_reviewer_suggestions VALUES (?, ?, ?, ?)",
                [
                    (url, i, s.login, s.commits)
                    for url, found in derived.suggestions.items()
                    for i, s in enumerate(found)
                ],
            )
            conn.executemany(
                "INSERT INTO github_team_scores VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (url, s.owned_files, s.required_files, s.my_files, s.part_files, s.part_lines)
                    for url, s in derived.team_scores.items()
                ],
            )

    def load(self) -> DerivedSnapshot:
        suggestions: defaultdict[str, list[ReviewerSuggestion]] = defaultdict(list)
        query = "SELECT * FROM github_reviewer_suggestions ORDER BY pr_url, position"
        for row in self._db.query(query):
            suggestions[row["pr_url"]].append(
                ReviewerSuggestion(login=row["login"], commits=row["commits"])
            )
        return DerivedSnapshot(
            suggestions=dict(suggestions),
            team_scores={
                row["pr_url"]: TeamPrScore(
                    owned_files=row["owned_files"],
                    required_files=row["required_files"],
                    my_files=row["my_files"],
                    part_files=row["part_files"],
                    part_lines=row["part_lines"],
                )
                for row in self._db.query("SELECT * FROM github_team_scores")
            },
        )
