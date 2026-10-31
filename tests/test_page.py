from datetime import UTC, datetime, timedelta

from move_forward.graph import Ref
from move_forward.page import Item, age

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_age() -> None:
    assert age(NOW, NOW - timedelta(minutes=5)) == "5m"
    assert age(NOW, NOW - timedelta(hours=5)) == "5h"
    assert age(NOW, NOW - timedelta(days=5)) == "5d"
    assert age(NOW, None) == "?"
    assert age(NOW, datetime(2026, 10, 29, 12)) == "1d"  # naive counts as UTC


def test_an_item_finds_its_subject_of_a_kind() -> None:
    pr = Ref(kind="pr", id="https://github.com/acme/widgets/pull/1")
    branch = Ref(kind="branch", id="/src/widgets\tme/a")
    item = Item(title="x", subjects=[pr, branch])
    assert item.subject("branch") == branch and item.subject("pr") == pr
    assert item.subject("ticket") is None
