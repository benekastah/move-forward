from datetime import UTC, datetime

from move_forward.db import Database
from move_forward.domain import LocalSnapshot
from move_forward.repositories import SnapshotRepository

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_snapshots_keep_payload_across_errors() -> None:
    db = Database(":memory:")
    repo = SnapshotRepository(db)
    repo.save("local", NOW, LocalSnapshot(repos=[]))
    repo.save_error("local", NOW, "boom")
    assert repo.get("local", LocalSnapshot) == LocalSnapshot(repos=[])
    assert repo.all()["local"].error == "boom"
    assert repo.get("missing", LocalSnapshot) is None
