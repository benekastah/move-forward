"""Snoozing a review from the command line, as Act now's Snooze button does."""

import re
from datetime import datetime, timedelta

from .act_now import ActNowConfig, Feedback
from .graph import Graph
from .kinds import PULL_REQUEST, REVIEW
from .repositories import FeedbackRepository

_DURATION = re.compile(r"(\d+)([dh])")


def parse_duration(text: str) -> timedelta:
    """`7d` or `12h` as a time span; raises ValueError for anything else, or for zero."""
    match = _DURATION.fullmatch(text)
    if match is None or int(match[1]) == 0:
        raise ValueError(f"{text!r} isn't a duration like 7d or 12h")
    amount = int(match[1])
    return timedelta(days=amount) if match[2] == "d" else timedelta(hours=amount)


def snooze_review(
    feedback: FeedbackRepository,
    graph: Graph,
    config: ActNowConfig,
    url: str,
    now: datetime,
    duration: timedelta | None = None,
) -> datetime:
    """Snooze the review of PR `url` until its head commit moves or `duration` passes (by
    default as long as `config` says for PRs); when the snooze ends. Raises LookupError when
    the PR isn't one github knows."""
    pr = graph.get(PULL_REQUEST, url)
    head = str(pr.attrs.get("head_oid") or "") if pr else ""
    if not head:
        raise LookupError(f"{url} isn't a PR the dashboard knows; run `mfd refresh`")
    until = now + duration if duration else config.snooze_until(PULL_REQUEST, now)
    feedback.say(
        (PULL_REQUEST, url, REVIEW), Feedback(action="snooze", fingerprint=head, until=until), now
    )
    return until


def wake_review(feedback: FeedbackRepository, url: str) -> None:
    """End the snooze of PR `url`'s review, if there is one."""
    feedback.forget((PULL_REQUEST, url, REVIEW))
