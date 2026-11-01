"""Local time: what the app shows, and the day it records as "today". Times are kept as
aware UTC datetimes; this turns them into the local zone."""

from datetime import date, datetime, tzinfo

zone: tzinfo | None = None
"""The local zone; None for the system's. Tests set it, since not every system can change
its own zone from Python (Windows has no `time.tzset`)."""


def local(when: datetime) -> datetime:
    """`when` in the local zone."""
    return when.astimezone(zone)


def day(when: datetime) -> date:
    """The local date at `when`."""
    return local(when).date()
