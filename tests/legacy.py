"""What older versions left in the database, for tests of the migrations that move it."""

from datetime import UTC, datetime

from pydantic import BaseModel

from move_forward.db import Database

WHEN = datetime(2026, 10, 30, 12, tzinfo=UTC)


def save_json_snapshot(db: Database, source: str, snapshot: BaseModel) -> None:
    """Keep `snapshot` as JSON in core's `snapshots`, as plugins did before their own tables;
    their migrations copy it in."""
    db.execute(
        "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, NULL)",
        (source, WHEN.isoformat(), snapshot.model_dump_json()),
    )
