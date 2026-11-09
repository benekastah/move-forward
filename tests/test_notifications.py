"""Notifications: events stored for subscribed kinds, told once per thing, and core's own."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, local_snapshot
from pydantic import ValidationError

from move_forward import notifications, refresh, registry
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Ref
from move_forward.notifications import Event, NotificationRepository, NotificationsConfig
from move_forward.page import Item, anchor
from move_forward.plugin import CollectRequest, EventsRequest, LoadRequest, StoreRequest
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.repositories import SnapshotRepository

KEEP = timedelta(hours=24)


def _event(kind: str = "pr_mergeable", thing: str = "pr:1", text: str = "can be merged") -> Event:
    return Event(kind=kind, thing=thing, title="PR 1", text=text, anchor="item-pr-1")


def test_events_about_the_same_thing_are_told_as_one() -> None:
    repo = NotificationRepository(Database(":memory:"))
    repo.add("github", [_event(), _event("ci_failed", text="CI failed"), _event(thing="pr:2")], NOW)
    first, second = repo.take(NOW, KEEP)
    assert (first.title, first.body, first.tag) == (
        "PR 1",
        "can be merged, CI failed",
        "github:pr:1",
    )
    assert first.url == "/#item-pr-1"
    assert second.tag == "github:pr:2"


def test_repeated_events_about_one_thing_do_not_hold_up_other_notifications() -> None:
    repo = NotificationRepository(Database(":memory:"))
    repo.add("github", [_event()] * (notifications.MAX_DELIVERED_AT_ONCE + 1), NOW)
    repo.add("github", [_event(thing="pr:2", text="new review")], NOW)
    assert [(n.tag, n.body) for n in repo.take(NOW, KEEP)] == [
        ("github:pr:1", "can be merged"),
        ("github:pr:2", "new review"),
    ]
    assert repo.take(NOW, KEEP) == []


def test_two_plugins_events_about_one_thing_are_told_apart() -> None:
    repo = NotificationRepository(Database(":memory:"))
    repo.add("github", [_event(thing="https://pr/1")], NOW)
    repo.add("slack", [_event(thing="https://pr/1", text="pinged")], NOW)
    assert [(n.tag, n.body) for n in repo.take(NOW, KEEP)] == [
        ("github:https://pr/1", "can be merged"),
        ("slack:https://pr/1", "pinged"),
    ]


def test_events_are_told_once() -> None:
    repo = NotificationRepository(Database(":memory:"))
    repo.add("github", [_event()], NOW)
    assert len(repo.take(NOW, KEEP)) == 1
    assert repo.take(NOW, KEEP) == []


def test_events_older_than_the_keep_time_are_dropped_untold() -> None:
    repo = NotificationRepository(Database(":memory:"))
    repo.add("github", [_event()], NOW - KEEP - timedelta(minutes=1))
    repo.add("github", [_event(thing="pr:2")], NOW - timedelta(hours=1))
    assert [n.tag for n in repo.take(NOW, KEEP)] == ["github:pr:2"]


def test_an_event_without_an_anchor_opens_the_dashboard() -> None:
    repo = NotificationRepository(Database(":memory:"))
    repo.add("dashboard", [Event(kind="x", thing="t", title="T", text="happened")], NOW)
    assert [n.url for n in repo.take(NOW, KEEP)] == ["/"]


def test_an_item_gets_the_anchor_of_its_main_subject() -> None:
    item = Item(title="PR", subjects=[Ref(kind="pull_request", id="https://github.com/a/b/pull/1")])
    assert item.anchor == anchor("pull_request", "https://github.com/a/b/pull/1")
    assert item.anchor is not None and " " not in item.anchor and "/" not in item.anchor
    assert Item(title="no subject").anchor is None


def _failing(db: Database, error: str | None, now: datetime = NOW) -> None:
    repo = SnapshotRepository(db)
    if error is None:
        repo.save_fetched("github", now)
    else:
        repo.save_error("github", now, error)


def test_a_plugin_failing_long_enough_is_said_once_until_it_works_again() -> None:
    db = Database(":memory:")
    config = NotificationsConfig(failing_minutes=15)
    _failing(db, "boom")
    assert notifications.dashboard_events(db, config, NOW) == []
    later = NOW + timedelta(minutes=16)
    [event] = notifications.dashboard_events(db, config, later)
    assert event.kind == "source_failing" and "boom" in event.text
    assert notifications.dashboard_events(db, config, later + timedelta(minutes=5)) == []
    _failing(db, None)
    assert notifications.dashboard_events(db, config, later + timedelta(minutes=6)) == []
    _failing(db, "again")
    assert notifications.dashboard_events(db, config, later + timedelta(minutes=7)) == []
    assert notifications.dashboard_events(db, config, later + timedelta(minutes=30))


class _Git(GitPlugin):
    """git that says "changed" whenever its snapshot differs, and keeps it in kv."""

    notification_kinds: ClassVar[dict[str, str]] = {"changed": "the snapshot changed"}
    repos: ClassVar[list[str]] = ["one"]
    broken: ClassVar[bool] = False

    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        return local_snapshot()

    def store(self, req: StoreRequest[GitConfig, LocalSnapshot]) -> None:
        req.ctx.kv.set("snapshot", json.dumps(self.repos))

    def load(self, req: LoadRequest[GitConfig]) -> LocalSnapshot | None:
        ctx = req.ctx
        return local_snapshot() if ctx.kv.get("snapshot") else None

    def events(self, req: EventsRequest[GitConfig, LocalSnapshot]) -> list[Event]:
        if self.broken:
            raise RuntimeError("no idea")
        return [Event(kind="changed", thing="git", title="git", text="changed")]


@pytest.fixture
def git(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = {"git": _Git()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setattr(_Git, "broken", False)


def _run(subscribe: list[str], times: int = 2) -> NotificationRepository:
    config = Config.model_validate(
        {"email": "me@x", "git": {"repos": []}, "notifications": {"subscribe": subscribe}}
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    for _ in range(times):
        refresh.refresh(config, db, NOW)
    return NotificationRepository(db)


@pytest.mark.usefixtures("git")
def test_a_subscribed_kind_is_stored_from_the_second_refresh() -> None:
    assert _run(["git.changed"], times=1).take(NOW, KEEP) == []  # nothing to compare with
    assert [n.body for n in _run(["git.changed"]).take(NOW, KEEP)] == ["changed"]


@pytest.mark.usefixtures("git")
def test_without_a_subscription_nothing_is_stored() -> None:
    assert _run([]).take(NOW, KEEP) == []


@pytest.mark.usefixtures("git")
def test_a_plugin_that_fails_to_say_what_changed_doesnt_stop_the_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_Git, "broken", True)
    repo = _run(["git.changed"])
    assert repo.take(NOW, KEEP) == []
    assert SnapshotRepository(repo._db).all()["git"].error is None  # pyright: ignore[reportPrivateUsage]


def test_only_known_kinds_can_be_subscribed_to() -> None:
    with pytest.raises(ValidationError, match="no such kind"):
        Config.model_validate({"email": "me@x", "notifications": {"subscribe": ["git.nope"]}})
    config = Config.model_validate(
        {"email": "me@x", "notifications": {"subscribe": ["dashboard.source_failing"]}}
    )
    assert config.notifications.subscribe == ["dashboard.source_failing"]


def test_the_dashboards_own_events_are_stored_when_subscribed(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "email": "me@x",
            "db_path": str(tmp_path / "x.db"),
            "notifications": {"subscribe": ["dashboard.source_failing"], "failing_minutes": 1},
        }
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    SnapshotRepository(db).save_error("github", NOW, "boom")
    refresh.refresh(config, db, NOW)
    refresh.refresh(config, db, NOW + timedelta(minutes=2))
    [n] = NotificationRepository(db).take(NOW + timedelta(minutes=2), KEEP)
    assert n.tag == "dashboard:source:github"


@pytest.mark.usefixtures("git")
def test_only_the_subscribed_kinds_of_a_plugin_are_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _Git,
        "notification_kinds",
        {"changed": "the snapshot changed", "other": "something else"},
    )

    def both(*_args: object) -> list[Event]:
        return [
            Event(kind="changed", thing="a", title="A", text="changed"),
            Event(kind="other", thing="b", title="B", text="other"),
        ]

    monkeypatch.setattr(_Git, "events", both)
    assert [n.tag for n in _run(["git.changed"]).take(NOW, KEEP)] == ["git:a"]
