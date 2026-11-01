"""github telling that something changed, from my notifications."""

from datetime import timedelta

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.plugin import Context
from move_forward.plugins.github import notifications
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.repositories import KvRepository
from move_forward.shell import CommandError

FIRST = "Fri, 30 Oct 2026 11:00:00 GMT"
SECOND = "Fri, 30 Oct 2026 12:02:00 GMT"
THIRD = "Fri, 30 Oct 2026 12:03:00 GMT"


def _answer(last_modified: str, *owners_and_times: tuple[str, str]) -> str:
    """What `gh api --include notifications` prints: headers, a blank line, then JSON."""
    body = ",".join(
        f'{{"repository": {{"owner": {{"login": "{owner}"}}}}, "updated_at": "{when}"}}'
        for owner, when in owners_and_times
    )
    return f"HTTP/2.0 200 OK\nLast-Modified: {last_modified}\nX-Poll-Interval: 60\r\n\r\n[{body}]"


def test_the_plugin_says_changed_for_a_new_notification_in_its_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = [
        _answer(FIRST, ("acme", "2026-10-30T10:00:00Z")),
        "304",
        _answer(SECOND, ("someone-else", "2026-10-30T12:01:30Z"), ("acme", "2026-10-30T10:00:00Z")),
        _answer(THIRD, ("ACME", "2026-10-30T12:02:30Z"), ("acme", "2026-10-30T10:00:00Z")),
    ]
    asked: list[list[str]] = []

    def run(args: list[str]) -> str:
        asked.append(args)
        answer = answers.pop(0)
        if answer == "304":
            raise CommandError(f"{' '.join(args)}: gh: HTTP 304")
        return answer

    monkeypatch.setattr(notifications, "run", run)
    db = Database(":memory:")
    plugin = GitHubPlugin()

    def changed(minutes: int) -> bool:
        ctx = Context(
            config=GitHubConfig(org="acme"),
            email="me@acme.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(minutes=minutes),
            db=db,
            kv=KvRepository(db, "github"),
        )
        return plugin.changed(ctx)

    assert not changed(0)  # the first look only notes where things stand
    assert not changed(0) and len(asked) == 1  # GitHub's poll interval isn't up
    assert not changed(1)  # not modified
    assert asked[-1][-2:] == ["-H", f"If-Modified-Since: {FIRST}"]
    assert not changed(2)  # new, but not in the org
    assert changed(3)  # new in the org, whatever the case of its name
    assert answers == []


def test_a_failure_other_than_not_modified_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str]) -> str:
        raise CommandError("gh api: HTTP 401: Bad credentials")

    monkeypatch.setattr(notifications, "run", run)
    with pytest.raises(CommandError, match="401"):
        notifications.fetch(None)
