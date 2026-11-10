"""github telling that something changed, from my notifications."""

import json
from datetime import timedelta

import pytest
from conftest import NOW, pr, snapshot

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import ChangedRequest, Context
from move_forward.plugins.github import notifications
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import CHANGED, EVERYTHING, GitHubPlugin
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.github.tables import MIGRATIONS
from move_forward.repositories import KvRepository, ProblemRepository
from move_forward.shell import CommandError

FIRST = "Fri, 30 Oct 2026 11:00:00 GMT"
SECOND = "Fri, 30 Oct 2026 12:02:00 GMT"
THIRD = "Fri, 30 Oct 2026 12:03:00 GMT"
FOURTH = "Fri, 30 Oct 2026 12:05:00 GMT"
FIFTH = "Fri, 30 Oct 2026 12:06:00 GMT"
SIXTH = "Fri, 30 Oct 2026 12:07:00 GMT"


def _note(owner: str, when: str, pr: int | None = 7, kind: str = "PullRequest") -> str:
    """A notification about PR `pr` of `owner`/widgets, or with `pr` None about a `kind` of
    thing that isn't a PR."""
    subject = (
        {"type": kind, "url": f"https://api.github.com/repos/{owner}/widgets/pulls/{pr}"}
        if pr is not None
        else {"type": kind, "url": None}
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
        _answer(FOURTH, _note("acme", "2026-10-30T12:04:30Z", None, "CheckSuite")),
        _answer(FIFTH, _note("acme", "2026-10-30T12:05:30Z", None, "Issue")),
        _answer(SIXTH, _note("acme", "2026-10-30T12:06:30Z", None, "SomethingNew")),
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
    db.migrate("github", MIGRATIONS)
    PrRepository(db).replace(snapshot(mine=[pr(1)], direct_requests=[pr(2, author="alice")]))
    plugin = GitHubPlugin()
    kv = KvRepository(db, "github")

    def taken() -> list[str]:
        """What `collect` would fetch, taken as it takes it."""
        found = json.loads(kv.get(CHANGED) or "[]")
        kv.delete(CHANGED)
        return found

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
        return bool(plugin.changed(ChangedRequest(ctx, Graph([]))))

    assert not changed(0)  # the first look only notes where things stand
    assert not changed(0) and len(asked) == 1  # GitHub's poll interval isn't up
    assert not changed(1)  # not modified
    assert asked[-1][-2:] == ["-H", f"If-Modified-Since: {FIRST}"]
    assert not changed(2)  # new, but not in the org
    assert changed(3)  # new in the org, whatever the case of its name
    assert taken() == ["ACME/widgets#8"]
    assert changed(5)  # a CI run names its repo, not its PR: my open PRs there
    assert taken() == ["acme/widgets#1"]
    assert not changed(6)  # an issue: no PR search shows it
    assert changed(7)  # something new to us: everything
    assert taken() == [EVERYTHING]
    assert answers == []


def test_a_failure_other_than_not_modified_is_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str]) -> str:
        raise CommandError("gh api: HTTP 401: Bad credentials")

    monkeypatch.setattr(notifications, "run", run)
    with pytest.raises(CommandError, match="401"):
        notifications.fetch("github.com", None)


def _context(db: Database, minutes: int = 0, **config: object) -> Context[GitHubConfig]:
    return Context(
        config=GitHubConfig.model_validate({"org": "acme", **config}),
        email="me@acme.com",
        teams=[],
        prune_days=90,
        now=NOW + timedelta(minutes=minutes),
        db=db,
        kv=KvRepository(db, "github"),
    )


def test_a_poll_that_fails_is_a_problem_until_one_works_and_tries_again_later(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    answers: list[str] = []

    failed = "gh api: HTTP 502: Bad Gateway"

    def run(args: list[str]) -> str:
        calls.append("poll")
        if not answers:
            raise CommandError(failed)
        return answers.pop(0)

    monkeypatch.setattr(notifications, "run", run)
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    plugin = GitHubPlugin()
    for minutes in (0, 0, 1, 2):
        assert not plugin.changed(ChangedRequest(_context(db, minutes), Graph([])))
    assert calls == ["poll", "poll", "poll"]  # not again within the minute
    said = f"can't read GitHub notifications: {failed}"
    assert ProblemRepository(db).of([""]) == {"": [said]}
    # Kept across a restart, so it isn't warned of again.
    assert not GitHubPlugin().changed(ChangedRequest(_context(db, 3), Graph([])))
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [said]
    answers.append(_answer(FIRST))
    assert not plugin.changed(ChangedRequest(_context(db, 4), Graph([])))
    assert ProblemRepository(db).of([""]) == {}


def test_searching_everywhere_hears_of_every_owners_prs(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        _answer(FIRST),
        _answer(SECOND, _note("someone-else", "2026-10-30T12:01:30Z")),
    ]

    def run(args: list[str]) -> str:
        return answers.pop(0)

    monkeypatch.setattr(notifications, "run", run)
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    plugin = GitHubPlugin()
    assert not plugin.changed(ChangedRequest(_context(db, search_scope="everywhere"), Graph([])))
    found = plugin.changed(ChangedRequest(_context(db, 2, search_scope="everywhere"), Graph([])))
    assert found
    assert json.loads(KvRepository(db, "github").get(CHANGED) or "[]") == ["someone-else/widgets#7"]


def test_a_token_that_cant_read_notifications_is_logged_once_a_run_not_a_problem(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    refused = "gh: Resource not accessible by personal access token (HTTP 403)"
    calls: list[str] = []

    def run(args: list[str]) -> str:
        calls.append("poll")
        raise CommandError(refused)

    monkeypatch.setattr(notifications, "run", run)
    db = Database(":memory:")
    db.migrate("github", MIGRATIONS)
    ProblemRepository(db).set("", "notifications", "can't read GitHub notifications: blip", NOW)
    plugin = GitHubPlugin()
    for minutes in (0, 0, 1, 2):
        assert not plugin.changed(ChangedRequest(_context(db, minutes), Graph([])))
    assert calls == ["poll", "poll", "poll"]  # not again within the minute
    assert ProblemRepository(db).of([""]) == {}  # nothing I can fix but the token
    said = f"can't read GitHub notifications (a fine-grained token can't): {refused}"
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [said]
    # Once each run, as the log of a run is read on its own.
    assert not GitHubPlugin().changed(ChangedRequest(_context(db, 3), Graph([])))
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [said] * 2
