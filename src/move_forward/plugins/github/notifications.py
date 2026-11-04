"""GitHub's notifications API, for `GitHubPlugin.changed`: polled with `If-Modified-Since`,
which it answers with a cheap "304 Not Modified" when nothing's new, as GitHub asks
pollers to do (no more often than its `X-Poll-Interval`)."""

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from pydantic import BaseModel, ConfigDict, TypeAdapter

from ...api.shell import CommandError, run

DEFAULT_POLL_SECONDS = 60
_HEAD_END = re.compile(r"\r?\n\r?\n")


class _Owner(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    login: str


class _Repository(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    owner: _Owner
    full_name: str


class _Subject(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    type: str
    url: str | None = None
    """The API URL of what it's about, e.g. `.../repos/acme/widgets/pulls/7`; None for some
    kinds (a CI run)."""


class _Notification(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    repository: _Repository
    subject: _Subject
    updated_at: datetime


_PULL_NUMBER = re.compile(r"/pulls/(\d+)$")
CHECK_SUITE = "CheckSuite"
"""A workflow run (CI) on something I pushed. It names its repo but not its PR."""
UNRELATED = frozenset(
    {
        "Issue",
        "Discussion",
        "TeamDiscussion",
        "Release",
        "Commit",
        "RepositoryVulnerabilityAlert",
        "RepositoryDependabotAlertsThread",
        "RepositoryAdvisory",
        "SecurityAdvisoryCredit",
    }
)
"""What notifications can be about that no PR search shows: issues, discussions, releases,
comments on commits, security alerts."""


@dataclass(frozen=True)
class Notification:
    owner: str
    """The repository's owner, an org or a user."""
    repo: str
    """`owner/name`."""
    kind: str
    """What it's about: `PullRequest`, `CheckSuite`, `Issue` and so on (GitHub's subject
    type)."""
    pr: int | None
    """The number of the PR it's about; None when it isn't about a PR (an issue, a CI run)."""
    updated_at: datetime


_NOTIFICATIONS = TypeAdapter(list[_Notification])


@dataclass(frozen=True)
class Notifications:
    """My unread notifications, as of `last_modified`."""

    last_modified: str
    """The response's `Last-Modified`, to send back as `If-Modified-Since`."""
    poll_seconds: int
    """How long GitHub asks to wait before polling again."""
    found: list[Notification]

    def new_in(self, org: str, since: str) -> list[Notification]:
        """Those in `org` updated after `since`, an earlier `last_modified`."""
        after = parsedate_to_datetime(since)
        return [n for n in self.found if n.owner.lower() == org.lower() and n.updated_at > after]


def _notification(raw: _Notification) -> Notification:
    number = _PULL_NUMBER.search(raw.subject.url or "")
    is_pr = raw.subject.type == "PullRequest" and number is not None
    return Notification(
        owner=raw.repository.owner.login,
        repo=raw.repository.full_name,
        kind=raw.subject.type,
        pr=int(number.group(1)) if is_pr and number else None,
        updated_at=raw.updated_at,
    )


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
        found=[_notification(n) for n in _NOTIFICATIONS.validate_json(body)],
    )
