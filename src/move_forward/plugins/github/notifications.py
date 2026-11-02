"""GitHub's notifications API, for `GitHubPlugin.changed`: polled with `If-Modified-Since`,
which it answers with a cheap "304 Not Modified" when nothing's new, as GitHub asks
pollers to do (no more often than its `X-Poll-Interval`)."""

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from pydantic import BaseModel, ConfigDict, TypeAdapter

from ...shell import CommandError, run

DEFAULT_POLL_SECONDS = 60
_HEAD_END = re.compile(r"\r?\n\r?\n")


class _Owner(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    login: str


class _Repository(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    owner: _Owner


class _Notification(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    repository: _Repository
    updated_at: datetime


_NOTIFICATIONS = TypeAdapter(list[_Notification])


@dataclass(frozen=True)
class Notifications:
    """My unread notifications, as of `last_modified`."""

    last_modified: str
    """The response's `Last-Modified`, to send back as `If-Modified-Since`."""
    poll_seconds: int
    """How long GitHub asks to wait before polling again."""
    updated: list[tuple[str, datetime]]
    """Each notification's repository owner (an org or a user) and when it was updated."""

    def new_in(self, org: str, since: str) -> bool:
        """Whether one in `org` was updated after `since`, an earlier `last_modified`."""
        after = parsedate_to_datetime(since)
        return any(owner.lower() == org.lower() and when > after for owner, when in self.updated)


def fetch(host: str, if_modified_since: str | None) -> Notifications | None:
    """My unread notifications on `host`, or None when nothing changed since
    `if_modified_since`."""
    args = ["gh", "api", "--hostname", host, "--include", "notifications"]
    if if_modified_since:
        args += ["-H", f"If-Modified-Since: {if_modified_since}"]
    try:
        out = run(args)
    except CommandError as e:
        if str(e).endswith("HTTP 304"):  # gh exits 1 on it
            return None
        raise
    head, body = _HEAD_END.split(out, maxsplit=1)
    headers = {
        name.strip().lower(): value.strip()
        for line in head.splitlines()[1:]
        if ":" in line
        for name, value in [line.split(":", 1)]
    }
    interval = headers.get("x-poll-interval", "")
    return Notifications(
        last_modified=headers["last-modified"],
        poll_seconds=int(interval) if interval.isdigit() else DEFAULT_POLL_SECONDS,
        updated=[
            (n.repository.owner.login, n.updated_at) for n in _NOTIFICATIONS.validate_json(body)
        ],
    )
