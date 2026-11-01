from datetime import UTC, datetime, timedelta

from move_forward.db import Database
from move_forward.repositories import SnapshotRepository

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_a_collectors_status_is_its_last_run_and_its_error() -> None:
    repo = SnapshotRepository(Database(":memory:"))
    repo.save_fetched("git", NOW)
    assert repo.all()["git"].error is None
    repo.save_error("git", NOW + timedelta(minutes=5), "boom")
    assert repo.all()["git"].error == "boom"
    repo.save_fetched("git", NOW + timedelta(minutes=10))
    assert repo.all()["git"].error is None
    assert repo.all()["git"].fetched_at == NOW + timedelta(minutes=10)
