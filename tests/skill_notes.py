"""Notes as the move-forward skill keeps them in markdown, for tests importing them."""

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
