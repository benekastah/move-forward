"""github telling that something changed, from my notifications."""

import json
from datetime import timedelta

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.github import notifications
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import CHANGED, EVERYTHING, GitHubPlugin
from move_forward.repositories import KvRepository
from move_forward.shell import CommandError

FIRST = "Fri, 30 Oct 2026 11:00:00 GMT"
SECOND = "Fri, 30 Oct 2026 12:02:00 GMT"
THIRD = "Fri, 30 Oct 2026 12:03:00 GMT"
FOURTH = "Fri, 30 Oct 2026 12:05:00 GMT"


def _note(owner: str, when: str, pr: int | None = 7) -> str:
    """A notification about PR `pr` of `owner`/widgets, or about a CI run when None."""
    subject = (
        {"type": "PullRequest", "url": f"https://api.github.com/repos/{owner}/widgets/pulls/{pr}"}
        if pr is not None
        else {"type": "CheckSuite", "url": None}
    )
    return json.dumps(
        {
            "repository": {"owner": {"login": owner}, "full_name": f"{owner}/widgets"},
            "subject": subject,
            "updated_at": when,
        }
    )


def _answer(last_modified: str, *notes: str) -> str:
    """What `gh api --include notifications` prints: headers, a blank line, then JSON."""
    return (
        f"HTTP/2.0 200 OK\nLast-Modified: {last_modified}\nX-Poll-Interval: 60\r\n\r\n"
        f"[{','.join(notes)}]"
    )


def test_the_plugin_says_changed_for_a_new_notification_in_its_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = [
        _answer(FIRST, _note("acme", "2026-10-30T10:00:00Z")),
        "304",
        _answer(
            SECOND,
            _note("someone-else", "2026-10-30T12:01:30Z"),
            _note("acme", "2026-10-30T10:00:00Z"),
        ),
        _answer(
            THIRD, _note("ACME", "2026-10-30T12:02:30Z", 8), _note("acme", "2026-10-30T10:00:00Z")
        ),
        _answer(FOURTH, _note("acme", "2026-10-30T12:04:30Z", None)),
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
    kv = KvRepository(db, "github")

    def changed(minutes: int) -> bool:
        ctx = Context(
            config=GitHubConfig(org="acme"),
            email="me@acme.com",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(minutes=minutes),
            db=db,
            kv=kv,
        )
        return plugin.changed(ctx, Graph([]))

    assert not changed(0)  # the first look only notes where things stand
    assert not changed(0) and len(asked) == 1  # GitHub's poll interval isn't up
    assert not changed(1)  # not modified
    assert asked[-1][-2:] == ["-H", f"If-Modified-Since: {FIRST}"]
    assert not changed(2)  # new, but not in the org
    assert changed(3)  # new in the org, whatever the case of its name
    assert json.loads(kv.get(CHANGED) or "") == ["ACME/widgets#8"]  # what collect fetches
    assert changed(5)  # a CI run: not a PR, so everything
    assert json.loads(kv.get(CHANGED) or "") == [EVERYTHING, "ACME/widgets#8"]
    assert answers == []


def test_a_failure_other_than_not_modified_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str]) -> str:
        raise CommandError("gh api: HTTP 401: Bad credentials")

    monkeypatch.setattr(notifications, "run", run)
    with pytest.raises(CommandError, match="401"):
        notifications.fetch("github.com", None)
