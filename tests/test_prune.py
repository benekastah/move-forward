"""Core's daily prune: when it runs the plugins' prunes."""

from datetime import UTC, datetime, timedelta

from move_forward import prune
from move_forward.db import Database

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def test_plugins_prune_once_a_day() -> None:
    db = Database(":memory:")
    runs: list[datetime] = []

    def plugins() -> list[str]:
        runs.append(NOW)
        return []

    prune.prune_if_due(db, NOW, plugins)
    prune.prune_if_due(db, NOW + timedelta(hours=1), plugins)
    assert len(runs) == 1
    prune.prune_if_due(db, NOW + timedelta(days=1), plugins)
    assert len(runs) == 2
