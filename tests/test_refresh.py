"""The refresh: plugins' snapshots collected and stored, including those that need the graph."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, issue, local_branch, local_snapshot, panel, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.act_now import OPENED_FOR, ActNowConfig, Feedback
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Ref
from move_forward.notifications import NotificationRepository
from move_forward.page import Badge, Item
from move_forward.plugin import (
    ActionError,
    AfterCollectRequest,
    CollectAfterRequest,
    CollectRequest,
    LoadRequest,
    StoreRequest,
    Subjects,
    SubjectsRequest,
)
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git_stack.plugin import GitStackPlugin
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.jira.repository import IssueRepository
from move_forward.repositories import FeedbackRepository, SnapshotRepository


class _Git(GitPlugin):
    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        return local_snapshot()


class _JiraAfterGit(JiraPlugin):
    """Fetches an issue per repo git found, and remembers what after_collect saw."""

    seen_by_after_collect: ClassVar[list[str]] = []

    def collect_after(self, req: CollectAfterRequest[JiraConfig]) -> JiraSnapshot:
        graph = req.graph
        return JiraSnapshot(
            issues=[issue(summary=f"about {r.ref.id}") for r in graph.of_kind("git.repo")]
        )

    def after_collect(self, req: AfterCollectRequest[JiraConfig]) -> None:
        graph = req.graph
        self.seen_by_after_collect[:] = [t.ref.id for t in graph.of_kind("ticket")]


@pytest.fixture(autouse=True)
def plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = {"git": _Git(), "jira": _JiraAfterGit()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])


def test_a_plugin_collects_from_the_graph_of_the_others(tmp_path: Path) -> None:
    config = Config.model_validate(
        {
            "email": "me@x",
            "git": {"repos": []},
            "jira": {"site": "https://j"},
        }
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    stored = IssueRepository(db).load()
    assert [i.summary for i in stored.issues] == ["about acme/widgets"]
    assert _JiraAfterGit.seen_by_after_collect == ["https://jira/browse/APP-1"]


class _GitInKv(GitPlugin):
    """git, keeping its snapshot in kv (as a stand-in for its own tables), and failing to
    collect once `broken`."""

    broken: ClassVar[bool] = False

    def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        if self.broken:
            raise RuntimeError("git is gone")
        return local_snapshot()

    def store(self, req: StoreRequest[GitConfig, LocalSnapshot]) -> None:
        req.ctx.kv.set("snapshot", req.snapshot.model_dump_json())

    def load(self, req: LoadRequest[GitConfig]) -> LocalSnapshot | None:
        ctx = req.ctx
        stored = ctx.kv.get("snapshot")
        return LocalSnapshot.model_validate_json(stored) if stored else None


def test_a_plugin_can_keep_its_snapshot_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"git": _GitInKv()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = Config.model_validate({"email": "me@x", "git": {"repos": []}})
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    status = SnapshotRepository(db).all()["git"]
    assert status.error is None and status.fetched_at == NOW
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.get("git.repo", "acme/widgets") is not None
    # A failed collect keeps what the last good one stored, and says why.
    monkeypatch.setattr(_GitInKv, "broken", True)
    refresh.refresh(config, db, NOW)
    assert "git is gone" in (SnapshotRepository(db).all()["git"].error or "")
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.get("git.repo", "acme/widgets") is not None


class _GitThatCantStore(_GitInKv):
    def store(self, req: StoreRequest[GitConfig, LocalSnapshot]) -> None:
        raise RuntimeError("disk full")


def test_a_failing_store_is_recorded_like_a_failing_collect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swap = {"git": _GitThatCantStore()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = Config.model_validate({"email": "me@x", "git": {"repos": []}})
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    assert "disk full" in (SnapshotRepository(db).all()["git"].error or "")


def test_act_now_buttons_snooze_pin_and_unpin(monkeypatch: pytest.MonkeyPatch) -> None:
    config, db = stored(
        github=snapshot(direct_requests=[pr(1, author="alice"), pr(2, author="bob")])
    )

    def act() -> list[Item]:
        return refresh.build_dashboard(config, db, NOW).act_now()

    first, second = act()
    pin = next(a for a in second.actions if a.label == "Pin")
    assert refresh.run_action(config, db, pin.name, pin.fields) == "pinned"
    assert [i.title for i in act()] == [second.title, first.title]
    unpin = next(a for a in act()[0].actions if a.label == "Unpin")
    assert refresh.run_action(config, db, unpin.name, unpin.fields) == "unpinned"
    snooze = next(a for a in first.actions if a.label == "Snooze")
    until_changed = {**snooze.fields, "choice": snooze.choices["until it changes"]}
    assert refresh.run_action(config, db, snooze.name, until_changed) == "snoozed until it changes"
    snooze = next(a for a in second.actions if a.label == "Snooze")
    for_a_while = {**snooze.fields, "choice": snooze.choices["for a while"]}
    assert refresh.run_action(config, db, snooze.name, for_a_while) == "snoozed until tomorrow"
    assert act() == []
    said = {key[1]: f for key, f in FeedbackRepository(db).all().items()}
    assert {f.action for f in said.values()} == {"snooze"}
    assert said[first.subjects[0].id].until is None
    snoozed_until = said[second.subjects[0].id].until
    assert snoozed_until is not None and snoozed_until > NOW
    with pytest.raises(ActionError, match="unknown snooze length"):
        refresh.run_action(config, db, snooze.name, {**snooze.fields, "choice": "forever"})
    with pytest.raises(ActionError, match="unknown action"):
        refresh.run_action(config, db, "act_now/dismiss", snooze.fields)
    with pytest.raises(ActionError, match="missing field"):
        refresh.run_action(config, db, "act_now/pin", {})


def test_a_review_snoozed_in_its_panel_stays_away_for_the_days_set_for_prs() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    config = config.model_copy(update={"act_now": ActNowConfig(snooze_days={"pull_request": 7})})

    def reviews() -> list[Item]:
        return panel(refresh.build_dashboard(config, db, NOW), "github.reviews_for_me").items

    snooze = next(a for a in reviews()[0].actions if a.label == "Snooze")
    assert refresh.run_action(config, db, snooze.name, snooze.fields).startswith("snoozed until ")
    [waiting] = reviews()
    assert waiting.set_aside
    (said,) = FeedbackRepository(db).all().values()
    now = datetime.now(UTC)  # the snooze counts from the real time, not NOW
    assert said.until is not None and now + timedelta(days=6) < said.until <= now + timedelta(
        days=7
    )
    assert refresh.build_dashboard(config, db, NOW).act_now() == []
    unsnooze = next(a for a in waiting.actions if a.label == "Unsnooze")
    assert refresh.run_action(config, db, unsnooze.name, unsnooze.fields) == "back"
    assert not reviews()[0].set_aside
    assert refresh.build_dashboard(config, db, NOW).act_now() != []


def test_a_snoozed_review_returns_when_the_prs_head_moves() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice", head_oid="a1")]))
    snooze = next(
        a
        for a in panel(refresh.build_dashboard(config, db, NOW), "github.reviews_for_me")
        .items[0]
        .actions
        if a.label == "Snooze"
    )
    refresh.run_action(config, db, snooze.name, snooze.fields)
    config, db2 = stored(github=snapshot(direct_requests=[pr(1, author="alice", head_oid="b2")]))
    for fb in FeedbackRepository(db).all().items():
        FeedbackRepository(db2).say(fb[0], fb[1], NOW)
    assert len(panel(refresh.build_dashboard(config, db2, NOW), "github.reviews_for_me").items) == 1


def test_feedback_that_is_over_or_old_is_pruned() -> None:
    db = Database(":memory:")
    feedback = FeedbackRepository(db)
    feedback.say(
        ("pull_request", "1", ""), Feedback(action="snooze", fingerprint="f", until=NOW), NOW
    )
    feedback.say(
        ("pull_request", "2", ""),
        Feedback(action="snooze", fingerprint="f"),  # until it changes
        NOW - timedelta(days=100),
    )
    feedback.say(("pull_request", "3", ""), Feedback(action="pin", fingerprint="f"), NOW)
    feedback.say(
        ("pull_request", "4", ""),
        Feedback(action="snooze", fingerprint="f"),  # until it changes, said lately
        NOW,
    )
    assert feedback.prune(NOW + timedelta(seconds=1), NOW - timedelta(days=90)) == 2
    assert sorted(feedback.all()) == [("pull_request", "3", ""), ("pull_request", "4", "")]


def test_a_plugin_collects_after_the_graph_when_it_implements_collect_after() -> None:
    assert GitStackPlugin().collects_after_graph
    assert not GitPlugin().collects_after_graph


def test_refreshing_some_plugins_refreshes_only_those(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Git(GitPlugin):
        def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
            calls.append("git")
            return local_snapshot()

    class JiraOnGit(JiraPlugin):
        requires: ClassVar[list[str]] = ["git"]

        def collect_after(self, req: CollectAfterRequest[JiraConfig]) -> JiraSnapshot:
            calls.append("jira")
            return JiraSnapshot(issues=[])

        def after_collect(self, req: AfterCollectRequest[JiraConfig]) -> None:
            calls.append("jira after")

    class GitHub(GitHubPlugin):
        def collect(self, req: CollectRequest[GitHubConfig]) -> GitHubSnapshot:
            calls.append("github")
            return snapshot()

        def after_collect(self, req: AfterCollectRequest[GitHubConfig]) -> None:
            calls.append("github after")

    swap = {"git": Git(), "jira": JiraOnGit(), "github": GitHub()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = Config.model_validate(
        {
            "email": "me@x",
            "github": {"org": "acme"},
            "git": {"repos": []},
            "jira": {"site": "https://j"},
        }
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW, {"git"})
    assert calls == ["git"]  # jira is built on git, but refreshes on its own
    calls.clear()
    refresh.refresh(config, db, NOW, {"jira"})
    assert calls == ["jira", "jira after"]
    calls.clear()
    refresh.refresh(config, db, NOW, {"github"})
    assert calls == ["github", "github after"]


def test_each_plugin_refreshes_on_its_own_setting() -> None:
    config = Config.model_validate(
        {"email": "me@x", "github": {"org": "acme"}, "git": {"repos": [], "refresh_seconds": 30}}
    )
    plugins = registry.enabled(config, Database(":memory:"), NOW)
    seconds = {e.name: e.refresh_seconds for e in plugins}
    assert seconds == {"github": 300, "git": 30, "notes": None}


def test_an_item_opened_elsewhere_is_dimmed_in_its_place_until_it_changes() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    assert item.act_now_fields is not None and item.opened_at is None
    assert (
        refresh.run_action(config, db, "act_now/opened", json.loads(item.act_now_fields))
        == "opened"
    )
    (dimmed,) = refresh.build_dashboard(config, db, NOW).act_now()
    assert dimmed.title == item.title and dimmed.opened_at is not None
    assert any(isinstance(b, Badge) and b.text.startswith("opened") for b in dimmed.badges)

    moved = pr(1, author="alice", head_oid="b" * 40)  # pushed to since
    PrRepository(db).replace(snapshot(direct_requests=[moved]))
    (changed,) = refresh.build_dashboard(config, db, NOW).act_now()
    assert changed.opened_at is None  # its fingerprint moved: worth another look


def test_an_item_opened_elsewhere_is_dimmed_for_a_day_at_most() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    refresh.run_action(config, db, "act_now/opened", json.loads(item.act_now_fields or "{}"))
    later = datetime.now(UTC) + OPENED_FOR + timedelta(seconds=1)
    (shown,) = refresh.build_dashboard(config, db, later).act_now()
    assert shown.opened_at is None


def test_opening_elsewhere_keeps_a_pin() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    pin = next(a for a in item.actions if a.label == "Pin")
    refresh.run_action(config, db, pin.name, pin.fields)
    refresh.run_action(config, db, "act_now/opened", json.loads(item.act_now_fields or "{}"))
    (said,) = FeedbackRepository(db).all().values()
    assert said.action == "pin"


def test_opening_elsewhere_replaces_a_snooze_only_once_it_has_lapsed() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    snooze = next(a for a in item.actions if a.label == "Snooze")
    refresh.run_action(config, db, snooze.name, {**snooze.fields, "choice": "changed"})
    opened = json.loads(item.act_now_fields or "{}")
    refresh.run_action(config, db, "act_now/opened", opened)
    (said,) = FeedbackRepository(db).all().values()
    assert said.action == "snooze"  # still in force: kept

    moved = pr(1, author="alice", head_oid="b" * 40)  # pushed to since: the snooze is over
    PrRepository(db).replace(snapshot(direct_requests=[moved]))
    (back,) = refresh.build_dashboard(config, db, NOW).act_now()
    refresh.run_action(config, db, "act_now/opened", json.loads(back.act_now_fields or "{}"))
    (dimmed,) = refresh.build_dashboard(config, db, NOW).act_now()
    assert dimmed.opened_at is not None


_OPEN = pr(9, head="me/done")
_MERGED = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")


def _merging(
    monkeypatch: pytest.MonkeyPatch, subscribe: list[str], *fetched: GitHubSnapshot
) -> tuple[Config, Database]:
    """My PR on `me/done`, open in what's stored, which github's collects return as
    `fetched`, one per refresh; git has the branch."""
    local = local_snapshot(branches=[local_branch("me/done", upstream="origin/me/done")])
    _, db = stored(github=snapshot(mine=[_OPEN]), local=local)
    config = Config.model_validate(
        {
            "email": "me@acme.com",
            "teams": ["core"],
            "github": {"org": "acme"},
            "git": {"repos": []},
            "notifications": {"subscribe": subscribe},
        }
    )
    left = list(fetched)

    def github_now(self: GitHubPlugin, req: CollectRequest[GitHubConfig]) -> GitHubSnapshot:
        return left.pop(0)

    def git_now(self: GitPlugin, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        return local

    def nothing_after(self: GitHubPlugin, req: AfterCollectRequest[GitHubConfig]) -> None:
        return None

    monkeypatch.setattr(GitHubPlugin, "collect", github_now)
    monkeypatch.setattr(_Git, "collect", git_now)  # this module's git
    monkeypatch.setattr(GitHubPlugin, "after_collect", nothing_after)
    return config, db


def _told(db: Database) -> list[tuple[str, str]]:
    return sorted(
        (n.title, n.body) for n in NotificationRepository(db).take(NOW, timedelta(hours=1))
    )


_MERGE_TOLD = [
    (f"{_OPEN.repo}#9 PR 9", "was merged"),
    ("me/done: PR merged 1d ago", "can be deleted"),
]
"""What my PR merging tells, subscribed to github's pr_merged and git's branch_deletable."""


def test_events_compare_with_what_was_stored_before_the_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a restart too: what was stored when the dashboard stopped is what the first
    refresh compares with, so a branch that became deletable meanwhile is said once."""
    merged = snapshot(merged_recent=[_MERGED])
    open_ = snapshot(mine=[_OPEN])
    config, db = _merging(monkeypatch, ["git.branch_deletable"], open_, merged, merged)
    refresh.refresh(config, db, NOW)  # the dashboard's last before it stopped
    assert _told(db) == []
    refresh.refresh(config, db, NOW)  # the first after it started again
    assert _told(db) == [("me/done: PR merged 1d ago", "can be deleted")]
    refresh.refresh(config, db, NOW)
    assert _told(db) == []  # said once


def test_a_plugins_events_and_its_panels_notifications_fail_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """github's panels failing loses none of its events hook's; git's events failing
    loses none of its panels'."""
    merged = snapshot(merged_recent=[_MERGED])
    subscribe = ["github.pr_merged", "git.branch_deletable"]
    config, db = _merging(monkeypatch, subscribe, snapshot(mine=[_OPEN]), merged)
    refresh.refresh(config, db, NOW)

    def broken(*_args: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(GitHubPlugin, "panels", broken)
    monkeypatch.setattr(_Git, "events", broken)
    refresh.refresh(config, db, NOW)
    assert _told(db) == _MERGE_TOLD


def test_an_item_newly_shown_by_its_plugins_own_refresh_is_told_by_that_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """git's branch loses its upstream: the refresh that fetched that shows it in Cleanup, and
    tells so then, not a refresh later."""
    tracked = local_branch("me/gone", upstream="origin/me/gone")
    gone = local_branch("me/gone", upstream="origin/me/gone", gone=True)
    fetched = [local_snapshot(branches=[tracked]), local_snapshot(branches=[gone])]
    _, db = stored(local=fetched[0])
    config = Config.model_validate(
        {
            "email": "me@acme.com",
            "git": {"repos": []},
            "notifications": {"subscribe": ["git.branch_deletable"]},
        }
    )

    def git_now(self: GitPlugin, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        return fetched.pop(0)

    monkeypatch.setattr(_Git, "collect", git_now)
    refresh.refresh(config, db, NOW)
    assert _told(db) == []
    refresh.refresh(config, db, NOW)
    assert _told(db) == [("me/gone: upstream gone", "can be deleted")]


def test_a_refresh_that_sees_the_dashboard_stopping_still_tells_what_it_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next start compares with what this one stored, so it's told now or never. Only
    when the refresh gets this far: on the server's own shutdown its daemon thread may be
    frozen first (see `refresh`)."""
    merged = snapshot(merged_recent=[_MERGED])
    subscribe = ["github.pr_merged", "git.branch_deletable"]
    config, db = _merging(monkeypatch, subscribe, snapshot(mine=[_OPEN]), merged)
    refresh.refresh(config, db, NOW)
    monkeypatch.setattr(refresh.shell, "stopping", lambda: True)
    refresh.refresh(config, db, NOW)
    assert _told(db) == _MERGE_TOLD


def test_fetching_a_pr_again_tells_what_changed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """As a refresh does: against what was stored before, for every plugin, so the next
    refresh, seeing the same, tells nothing again."""
    merged = snapshot(merged_recent=[_MERGED])
    subscribe = ["github.pr_merged", "git.branch_deletable"]
    config, db = _merging(monkeypatch, subscribe, snapshot(mine=[_OPEN]), merged)
    refresh.refresh(config, db, NOW)

    def again(
        self: GitHubPlugin, req: SubjectsRequest[GitHubConfig, GitHubSnapshot]
    ) -> Subjects[GitHubSnapshot]:
        return Subjects(merged, req.refs)

    monkeypatch.setattr(GitHubPlugin, "refresh_subjects", again)
    pr_ref = Ref(kind="pull_request", id=_OPEN.url)
    assert refresh.refresh_subjects(config, db, [pr_ref], NOW) == set()
    assert _told(db) == _MERGE_TOLD
    refresh.refresh(config, db, NOW)
    assert _told(db) == []


def test_the_known_actions_are_the_enabled_plugins_declared_ones() -> None:
    config, db = stored()
    known = refresh._known_action(registry.enabled(config, db, NOW))  # pyright: ignore[reportPrivateUsage]
    assert known("notes/todo_done") and known("git/push_branch")
    assert not known("notes/nope") and not known("jira/transition")  # jira isn't on
