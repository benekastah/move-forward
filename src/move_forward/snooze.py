"""Snoozing from the command line, as the Snooze button does."""

import re
from collections.abc import Iterator
from datetime import timedelta

from .act_now import ACTIONS, UNTIL_CHANGED
from .graph import Ref
from .page import Item
from .panels import Dashboard

_DURATION = re.compile(r"(\d+)([dh])")
SNOOZE = f"{ACTIONS}/snooze"
"""The Snooze button's action."""


def parse_duration(text: str) -> timedelta:
    """`7d` or `12h` as a time span; raises ValueError for anything else, or for zero, naming
    `mfd snooze`'s other length too (`UNTIL_CHANGED`), which its caller checks for first."""
    match = _DURATION.fullmatch(text)
    if match is None or int(match[1]) == 0:
        raise ValueError(f"{text!r} isn't a duration like 7d or 12h, nor {UNTIL_CHANGED}")
    amount = int(match[1])
    return timedelta(days=amount) if match[2] == "d" else timedelta(hours=amount)


def _items(items: list[Item]) -> Iterator[Item]:
    for item in items:
        yield item
        yield from _items(item.parts)


def snooze_buttons(dashboard: Dashboard, ref: Ref, task: str | None = None) -> list[dict[str, str]]:
    """The fields of the Snooze buttons on the items about `ref` (their main subject), in Act
    now and in the panels, parts included; only `task`'s, given one. Run as the button runs
    them (`refresh.snooze_item`), they snooze what the buttons would, until it changes."""
    found: list[dict[str, str]] = []
    for item in _items([item for panel in dashboard.panels for item in panel.items]):
        for action in item.actions:
            fields = action.fields
            if (
                action.name == SNOOZE
                and (fields.get("kind"), fields.get("id")) == (ref.kind, ref.id)
                and task in (None, fields.get("task"))
                and fields not in found
            ):
                found.append(fields)
    return found
