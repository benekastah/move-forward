from datetime import UTC, datetime

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


def test_reviews_section_is_not_a_work_item_but_its_nudges_are_pings() -> None:
    pings = notes.parse_nudges(WORK_NOTES)
    assert [(p.pr_url, p.kind, p.sent_at) for p in pings] == [
        ("https://github.com/acme/widgets/pull/12", "first", datetime(2026, 10, 29, tzinfo=UTC)),
        ("https://github.com/acme/widgets/pull/13", "first", datetime(2026, 10, 29, tzinfo=UTC)),
    ]


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
