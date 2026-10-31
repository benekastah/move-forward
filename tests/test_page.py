from datetime import UTC, datetime, timedelta

from move_forward.page import age

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_age() -> None:
    assert age(NOW, NOW - timedelta(minutes=5)) == "5m"
    assert age(NOW, NOW - timedelta(hours=5)) == "5h"
    assert age(NOW, NOW - timedelta(days=5)) == "5d"
    assert age(NOW, None) == "?"
    assert age(NOW, datetime(2026, 10, 29, 12)) == "1d"  # naive counts as UTC
