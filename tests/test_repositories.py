from datetime import UTC, datetime

from move_forward.db import Database
from move_forward.domain import LocalSnapshot, Ping, ReviewerUser, TeamChannel
from move_forward.repositories import PingRepository, ReviewerRepository, SnapshotRepository

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
ALICE = ReviewerUser(login="alice", email="a@x", slack_id="U1")
CORE_TEAM = TeamChannel(slug="core", channel_name="#team", channel_id="C1")
IMPORTED = Ping(pr_url="https://pr/1", target="unknown", kind="first", sent_at=NOW)


def test_snapshots_keep_payload_across_errors() -> None:
    db = Database(":memory:")
    repo = SnapshotRepository(db)
    repo.save("local", NOW, LocalSnapshot(repos=[]))
    repo.save_error("local", NOW, "boom")
    assert repo.get("local", LocalSnapshot) == LocalSnapshot(repos=[])
    assert repo.all()["local"].error == "boom"
    assert repo.get("missing", LocalSnapshot) is None


def test_an_import_replaces_imported_rows_but_keeps_dashboard_and_lookup_rows() -> None:
    db = Database(":memory:")
    reviewers = ReviewerRepository(db)
    pings = PingRepository(db)
    reviewers.save_looked_up_email("bob", "b@x")
    pings.add(Ping(pr_url="https://pr/2", target="user:bob", kind="first", sent_at=NOW))

    reviewers.replace_imported([ALICE], [CORE_TEAM])
    pings.replace_imported([IMPORTED])
    assert set(reviewers.users()) == {"alice", "bob"}
    assert reviewers.teams()["core"].channel_id == "C1"
    assert sorted(p.pr_url for p in pings.all()) == ["https://pr/1", "https://pr/2"]

    reviewers.replace_imported([], [])
    pings.replace_imported([])
    assert set(reviewers.users()) == {"bob"} and reviewers.teams() == {}
    assert [p.pr_url for p in pings.all()] == ["https://pr/2"]


def test_the_reviewer_map_wins_over_lookups_but_lookups_never_overwrite() -> None:
    db = Database(":memory:")
    reviewers = ReviewerRepository(db)
    reviewers.replace_imported([ALICE], [])
    reviewers.save_looked_up_email("alice", "other@x")
    assert reviewers.users()["alice"].email == "a@x"
