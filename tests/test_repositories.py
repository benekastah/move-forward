from datetime import UTC, datetime

from move_forward.db import Database
from move_forward.domain import (
    IcedBranch,
    IcedStack,
    LocalSnapshot,
    Notes,
    Ping,
    ReviewerUser,
    TeamChannel,
    Todo,
    WorkItem,
)
from move_forward.repositories import (
    IcedStackRepository,
    NotesImportRepository,
    PingRepository,
    ReviewerRepository,
    SnapshotRepository,
    WorkItemRepository,
)

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def _notes(**overrides: object) -> Notes:
    fields: dict[str, object] = {
        "work_items": [
            WorkItem(
                title="APP-1 x",
                fields={"Status": ["open"]},
                todos=[Todo(text="a", size="quick")],
                text="t",
            )
        ],
        "pings": [Ping(pr_url="https://pr/1", target="unknown", kind="first", sent_at=NOW)],
        "iced_stacks": [
            IcedStack(bottom_branch="b", repo_dir="~/r", branches=[IcedBranch(name="b", sha="1")])
        ],
        "users": [ReviewerUser(login="alice", email="a@x", slack_id="U1")],
        "teams": [TeamChannel(slug="core", channel_name="#team", channel_id="C1")],
    }
    fields.update(overrides)
    return Notes.model_validate(fields)


def test_snapshots_keep_payload_across_errors() -> None:
    db = Database(":memory:")
    repo = SnapshotRepository(db)
    repo.save("local", NOW, LocalSnapshot(repos=[]))
    repo.save_error("local", NOW, "boom")
    assert repo.get("local", LocalSnapshot) == LocalSnapshot(repos=[])
    assert repo.all()["local"].error == "boom"
    assert repo.get("missing", LocalSnapshot) is None


def test_notes_import_replaces_imported_rows_but_keeps_dashboard_and_lookup_rows() -> None:
    db = Database(":memory:")
    reviewers = ReviewerRepository(db)
    pings = PingRepository(db)
    reviewers.save_looked_up_email("bob", "b@x")
    pings.add(Ping(pr_url="https://pr/2", target="user:bob", kind="first", sent_at=NOW))

    NotesImportRepository(db).replace(_notes())
    assert [w.title for w in WorkItemRepository(db).all()] == ["APP-1 x"]
    assert [s.bottom_branch for s in IcedStackRepository(db).all()] == ["b"]
    assert set(reviewers.users()) == {"alice", "bob"}
    assert reviewers.teams()["core"].channel_id == "C1"
    assert sorted(p.pr_url for p in pings.all()) == ["https://pr/1", "https://pr/2"]

    NotesImportRepository(db).replace(_notes(work_items=[], pings=[], users=[]))
    assert WorkItemRepository(db).all() == []
    assert set(reviewers.users()) == {"bob"}
    assert [p.pr_url for p in pings.all()] == ["https://pr/2"]


def test_notes_win_over_lookups_but_lookups_never_overwrite() -> None:
    db = Database(":memory:")
    reviewers = ReviewerRepository(db)
    NotesImportRepository(db).replace(_notes())
    reviewers.save_looked_up_email("alice", "other@x")
    assert reviewers.users()["alice"].email == "a@x"
