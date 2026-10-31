from datetime import UTC, datetime
from pathlib import Path

from move_forward import notes

WORK_NOTES = """\
# Work notes

Intro text.

## APP-1 Fix the widget
- Status: in review
- Where: engineering https://github.com/acme/widgets/pull/12, https://github.com/acme/widgets/pull/13
- Left to do:
  - [quick] reply to the thread
  - [big] get it merged
  - no size here
- Blocked on: reviews
- Merge conditions: don't merge until https://github.com/acme/widgets/pull/13 is merged
- Declined 2026-10-29: don't nudge today
- Declined 2026-10-28: skip the review
- Updated: 2026-10-29

## Thing without ticket (inferred)
- Left to do:
- Updated: 2026-10-28

## Reviews requested of me
- Offered 2026-10-30, picked: https://github.com/acme/widgets/pull/99
- Nudged 2026-10-29 (first reminders): https://github.com/acme/widgets/pull/12 (DM), https://github.com/acme/widgets/pull/13
"""

ON_ICE = """\
# Stacks on ice

Last asked: 2026-10-30

<!-- Format:
## <bottom branch> (<repo dir>)
-->

## me/bottom (~/src/widgets)
- Iced: 2026-10-28
- Note: parked
- Branches:
  - me/bottom abc123
  - me/top def456

## me/other (~/src/other)
- Iced: 2026-10-29
- Branches:
  - me/other 0000
"""

REVIEWER_MAP = """\
# Reviewer map

## Teams

| GitHub team | Slack channel | Notes |
| --- | --- | --- |
| core | #team-core-chat | my team |
| web | #team-web (C0123ABCD) | |

## Users

| GitHub login | Work email | Slack user ID |
| --- | --- | --- |
| alice | alice@example.com | U111 |
| `bob` | | |
"""


def test_work_items_fields_todos_and_dateless_keys() -> None:
    items = notes.parse_work_notes(WORK_NOTES)
    assert [i.title for i in items] == ["APP-1 Fix the widget", "Thing without ticket (inferred)"]
    item = items[0]
    assert item.fields["Status"] == ["in review"]
    assert item.fields["Blocked on"] == ["reviews"]
    assert item.fields["Merge conditions"] == [
        "don't merge until https://github.com/acme/widgets/pull/13 is merged"
    ]
    # Repeated dated keys merge under one undated key, in order.
    assert item.fields["Declined"] == ["don't nudge today", "skip the review"]
    assert "Left to do" not in item.fields
    assert [(t.size, t.text) for t in item.todos] == [
        ("quick", "reply to the thread"),
        ("big", "get it merged"),
        (None, "no size here"),
    ]
    assert items[1].todos == []


def test_reviews_section_is_not_a_work_item_but_its_nudges_are_pings() -> None:
    pings = notes.parse_nudges(WORK_NOTES)
    assert [(p.pr_url, p.kind, p.sent_at) for p in pings] == [
        ("https://github.com/acme/widgets/pull/12", "first", datetime(2026, 10, 29, tzinfo=UTC)),
        ("https://github.com/acme/widgets/pull/13", "first", datetime(2026, 10, 29, tzinfo=UTC)),
    ]


def test_on_ice() -> None:
    stacks = notes.parse_on_ice(ON_ICE)
    assert len(stacks) == 2
    assert stacks[0].bottom_branch == "me/bottom"
    assert stacks[0].repo_dir == "~/src/widgets"
    assert stacks[0].iced == "2026-10-28"
    assert stacks[0].note == "parked"
    assert [(b.name, b.sha) for b in stacks[0].branches] == [
        ("me/bottom", "abc123"),
        ("me/top", "def456"),
    ]
    assert stacks[1].note is None


def test_reviewer_map() -> None:
    users, teams = notes.parse_reviewer_map(REVIEWER_MAP)
    assert [(u.login, u.email, u.slack_id) for u in users] == [
        ("alice", "alice@example.com", "U111"),
        ("bob", None, None),
    ]
    assert [(t.slug, t.channel_name, t.channel_id) for t in teams] == [
        ("core", "#team-core-chat", None),
        ("web", "#team-web", "C0123ABCD"),
    ]


def test_load_tolerates_missing_files(tmp_path: Path) -> None:
    (tmp_path / notes.WORK_NOTES_FILE).write_text(WORK_NOTES)
    loaded = notes.load(tmp_path)
    assert len(loaded.work_items) == 2
    assert loaded.iced_stacks == []
    assert loaded.users == []
