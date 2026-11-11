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
from move_forward.db import CORE, Database
from move_forward.graph import Ref
from move_forward.notifications import Event, NotificationRepository, NotificationsConfig
from move_forward.page import Item, Notify, Panel, anchor
from move_forward.plugin import ContextRequest, RefreshRequest, Request
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.repositories import KvRepository, SnapshotRepository

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
    assert notifications.dashboard_events(db, config, NOW, ["github"]) == []
    later = NOW + timedelta(minutes=16)
    [event] = notifications.dashboard_events(db, config, later, ["github"])
    assert event.kind == "source_failing" and "boom" in event.text
    assert (
        notifications.dashboard_events(db, config, later + timedelta(minutes=5), ["github"]) == []
    )
    _failing(db, None)
    assert (
        notifications.dashboard_events(db, config, later + timedelta(minutes=6), ["github"]) == []
    )
    _failing(db, "again")
    assert (
        notifications.dashboard_events(db, config, later + timedelta(minutes=7), ["github"]) == []
    )
    assert notifications.dashboard_events(db, config, later + timedelta(minutes=30), ["github"])


class _Kept:
    """What `_Git` keeps: that it stored, and which repos then."""

    def __init__(self, kv: KvRepository, repos: list[str]):
        self.kv = kv
        self.repos = repos

    def save(self, snapshot: LocalSnapshot) -> None:
        self.kv.set("snapshot", json.dumps(self.repos))

    def load(self) -> LocalSnapshot | None:
        return local_snapshot() if self.kv.get("snapshot") else None


class _Git(GitPlugin):
    """git that says "changed" whenever its snapshot differs, and keeps it in kv."""

    notification_kinds: ClassVar[dict[str, str]] = {"changed": "the snapshot changed"}
    repos: ClassVar[list[str]] = ["one"]
    broken: ClassVar[bool] = False

    def collect(self, req: RefreshRequest[GitConfig]) -> LocalSnapshot:
        return local_snapshot()

    def snapshots(self, req: ContextRequest[GitConfig]) -> "_Kept":
        return _Kept(req.ctx.kv, self.repos)

    def events(self, req: Request[GitConfig, LocalSnapshot]) -> list[Event]:
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
def test_events_are_asked_after_a_refresh_of_anything_with_the_graph_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What another plugin's entities say may change what a plugin has to say, so it's asked
    even when it wasn't fetched: its snapshot then is the same before and after."""
    asked: list[tuple[bool, bool]] = []
    befores: list[refresh.Before] = []

    def record(self: _Git, req: Request[GitConfig, LocalSnapshot]) -> list[Event]:
        [before] = befores
        was = req.previous.graph is before.graph and before.graph is not req.graph
        asked.append((req.previous.snapshot == req.snapshot, was))
        return []

    def before_refresh(db: Database, plugins: list[registry.Enabled]) -> refresh.Before:
        befores.append(found := real_before_refresh(db, plugins))
        return found

    real_before_refresh = refresh.before_refresh
    monkeypatch.setattr(_Git, "events", record)
    config = Config.model_validate(
        {"email": "me@x", "git": {"repos": []}, "notifications": {"subscribe": ["git.changed"]}}
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)  # the first store: nothing to compare with
    monkeypatch.setattr(refresh, "before_refresh", before_refresh)
    refresh.refresh(config, db, NOW, only=set())
    assert asked == [(True, True)]


@pytest.mark.usefixtures("git")
def test_events_are_told_when_the_plugin_last_stored_what_they_compare_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh that failed stored nothing, so the snapshot before is still the one stored
    earlier, and so is the time."""
    told: list[datetime | None] = []

    def record(self: _Git, req: Request[GitConfig, LocalSnapshot]) -> list[Event]:
        told.append(req.previous.at)
        return []

    def fail(self: _Git, req: RefreshRequest[GitConfig]) -> LocalSnapshot:
        raise RuntimeError("offline")

    monkeypatch.setattr(_Git, "events", record)
    config = Config.model_validate(
        {"email": "me@x", "git": {"repos": []}, "notifications": {"subscribe": ["git.changed"]}}
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    with monkeypatch.context() as offline:
        offline.setattr(_Git, "collect", fail)
        refresh.refresh(config, db, NOW + timedelta(minutes=5))
    refresh.refresh(config, db, NOW + timedelta(minutes=10))
    assert told == [NOW, NOW]


@pytest.mark.usefixtures("git")
def test_a_snapshot_stored_before_the_time_was_kept_is_compared_with_at_no_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    told: list[tuple[bool, datetime | None]] = []

    def record(self: _Git, req: Request[GitConfig, LocalSnapshot]) -> list[Event]:
        told.append((req.previous.snapshot is not None, req.previous.at))
        return []

    monkeypatch.setattr(_Git, "events", record)
    config = Config.model_validate(
        {"email": "me@x", "git": {"repos": []}, "notifications": {"subscribe": ["git.changed"]}}
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    KvRepository(db, CORE).set(f"{refresh.STORED}git", "1")
    refresh.refresh(config, db, NOW + timedelta(minutes=5))
    assert told == [(True, None)]


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
    # A part of notes (always on), which the refresh doesn't overwrite.
    SnapshotRepository(db).save_error("notes.part", NOW, "boom")
    refresh.refresh(config, db, NOW)
    refresh.refresh(config, db, NOW + timedelta(minutes=2))
    [n] = NotificationRepository(db).take(NOW + timedelta(minutes=2), KEEP)
    assert n.tag == "dashboard:source:notes.part"


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


def test_a_notification_shows_a_strangers_title_without_hidden_characters() -> None:
    repo = NotificationRepository(Database(":memory:"))
    event = Event(
        kind="review_requested_me", thing="pr:1", title="Update \u202efdp.exe", text="asked\u200b"
    )
    repo.add("github", [event], NOW)
    [told] = repo.take(NOW, KEEP)
    assert (told.title, told.body) == ("Update fdp.exe", "asked")


def test_only_enabled_plugins_runs_are_sources() -> None:
    """A plugin taken out of the config, or removed, leaves its last run behind: it's no
    source any more, and its old failure says nothing."""
    db = Database(":memory:")
    repo = SnapshotRepository(db)
    repo.save_fetched("github", NOW)
    repo.save_fetched("github.derived", NOW)  # a part of github's
    repo.save_error("vcard", NOW, "gone")
    repo.save_fetched("gitXstack.part", NOW)  # not git_stack's: `_` is no wildcard here
    repo.save_fetched("GitHub.derived", NOW)  # nor github's: names are compared exactly
    assert sorted(repo.of(["github", "git_stack"])) == ["github", "github.derived"]
    config = NotificationsConfig(failing_minutes=15)
    later = NOW + timedelta(minutes=16)
    assert notifications.dashboard_events(db, config, later, ["github"]) == []


def _shown(*things: str, kind: str = "branch_deletable") -> list[Panel]:
    """A panel of items with a notification, one about each of `things` (a branch)."""
    items = [
        Item(
            title=f"{thing}: PR merged",
            subjects=[Ref(kind="git.branch", id=thing)],
            notify=Notify(kind=kind, text="can be deleted"),
        )
        for thing in things
    ]
    return [Panel(key="cleanup", title="Cleanup", description="", items=items)]


def test_an_item_newly_shown_with_a_notification_is_told_once_and_not_the_first_time() -> None:
    db = Database(":memory:")
    assert notifications.newly_shown(db, "git", _shown("a")) == []  # nothing to compare with
    [event] = notifications.newly_shown(db, "git", _shown("a", "b"))
    assert event == Event(
        kind="branch_deletable",
        thing="b",
        title="b: PR merged",
        text="can be deleted",
        anchor=anchor("git.branch", "b"),
    )
    assert notifications.newly_shown(db, "git", _shown("a", "b")) == []  # told once
    assert notifications.newly_shown(db, "git", _shown("a")) == []  # gone
    assert [e.thing for e in notifications.newly_shown(db, "git", _shown("a", "b"))] == ["b"]


def test_what_was_shown_is_kept_by_plugin_and_kind() -> None:
    db = Database(":memory:")
    notifications.newly_shown(db, "git", _shown("a"))
    notifications.newly_shown(db, "jira", [])
    assert notifications.newly_shown(db, "jira", _shown("a")) != []  # another plugin's
    assert notifications.newly_shown(db, "git", _shown("a", kind="other")) != []


def test_a_notification_names_its_own_thing_when_it_gives_one() -> None:
    db = Database(":memory:")
    notifications.newly_shown(db, "git", [])
    item = Item(title="no subject", notify=Notify(kind="k", text="t", thing="mine"))
    panel = Panel(key="p", title="P", description="", items=[item])
    found = notifications.newly_shown(db, "git", [panel])
    assert [(e.thing, e.anchor) for e in found] == [("mine", "")]


def test_an_item_without_subjects_must_name_its_notifications_thing() -> None:
    """Its title, which may carry an age, would make it new at each refresh."""
    with pytest.raises(ValidationError, match="its Notify must give a thing"):
        Item(title="me/x: PR merged 1d ago", notify=Notify(kind="k", text="t"))


def test_forgetting_what_a_plugin_showed_starts_it_afresh() -> None:
    db = Database(":memory:")
    notifications.newly_shown(db, "git", _shown("a"))
    notifications.newly_shown(db, "jira", _shown("a"))
    notifications.forget_shown(db, ["jira"])
    assert notifications.newly_shown(db, "git", _shown("a", "b")) == []  # as the first time
    assert notifications.newly_shown(db, "jira", _shown("a", "b")) != []
    notifications.forget_shown(db, [])
    assert notifications.newly_shown(db, "jira", _shown("c")) == []


def test_unsubscribing_forgets_what_the_panels_showed() -> None:
    """Subscribing again then doesn't tell all that joined the panels meanwhile."""
    db = Database(":memory:")
    for subscribe in (["git.branch_deletable"], []):
        config = Config.model_validate(
            {"email": "me@x", "git": {"repos": []}, "notifications": {"subscribe": subscribe}}
        )
        registry.migrate(config, db)
        refresh.refresh(config, db, NOW)
        shown = db.query("SELECT plugin FROM shown_plugins")
        assert [row["plugin"] for row in shown] == (["git"] if subscribe else [])


def test_what_an_item_notifies_of_is_not_part_of_the_page() -> None:
    item = Item(title="x", notify=Notify(kind="k", text="t", thing="x"))
    assert "notify" not in item.model_dump()
