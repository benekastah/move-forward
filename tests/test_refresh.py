"""The refresh: plugins' snapshots collected and stored, including those that need the graph."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, issue, local_branch, local_snapshot, panel, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.act_now import PENDING, ActNowConfig, Feedback
from move_forward.config import Config
from move_forward.db import Database
from move_forward.notifications import NotificationRepository
from move_forward.page import Item
from move_forward.plugin import (
    ActionError,
    AfterCollectRequest,
    CollectAfterRequest,
    Collected,
    CollectRequest,
    LoadRequest,
    StoreRequest,
)
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.git.models import LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.plugin import GitHubPlugin
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

    collects_after_graph: ClassVar[bool] = True
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
    assert _JiraAfterGit.seen_by_after_collect == ["APP-1"]


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


def test_act_now_buttons_snooze_dismiss_pin_and_unpin(monkeypatch: pytest.MonkeyPatch) -> None:
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
    dismiss = next(a for a in first.actions if a.label == "Dismiss")
    assert refresh.run_action(config, db, dismiss.name, dismiss.fields) == "dismissed"
    snooze = next(a for a in second.actions if a.label == "Snooze")
    assert refresh.run_action(config, db, snooze.name, snooze.fields) == "snoozed until tomorrow"
    assert act() == []
    said = {f.action: f for f in FeedbackRepository(db).all().values()}
    assert set(said) == {"dismiss", "snooze"}
    snoozed_until = said["snooze"].until
    assert snoozed_until is not None and snoozed_until > NOW
    with pytest.raises(ActionError, match="unknown action"):
        refresh.run_action(config, db, "act_now/forget", snooze.fields)
    with pytest.raises(ActionError, match="missing field"):
        refresh.run_action(config, db, "act_now/pin", {})


def test_a_review_snoozed_in_its_panel_stays_away_for_the_days_set_for_prs() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    config = config.model_copy(update={"act_now": ActNowConfig(snooze_days={"pull_request": 7})})

    def reviews() -> list[Item]:
        return panel(refresh.build_dashboard(config, db, NOW), "github.reviews_for_me").items

    snooze = next(a for a in reviews()[0].actions if a.label == "Snooze")
    assert refresh.run_action(config, db, snooze.name, snooze.fields).startswith("snoozed until ")
    assert reviews() == []
    (said,) = FeedbackRepository(db).all().values()
    now = datetime.now(UTC)  # the snooze counts from the real time, not NOW
    assert said.until is not None and now + timedelta(days=6) < said.until <= now + timedelta(
        days=7
    )
    assert refresh.build_dashboard(config, db, NOW).act_now() == []


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
        Feedback(action="dismiss", fingerprint="f"),
        NOW - timedelta(days=100),
    )
    feedback.say(("pull_request", "3", ""), Feedback(action="pin", fingerprint="f"), NOW)
    assert feedback.prune(NOW + timedelta(seconds=1), NOW - timedelta(days=90)) == 2
    assert list(feedback.all()) == [("pull_request", "3", "")]


def test_refreshing_some_plugins_brings_the_ones_built_on_them_along(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Git(GitPlugin):
        def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
            calls.append("git")
            return local_snapshot()

    class JiraOnGit(JiraPlugin):
        collects_after_graph: ClassVar[bool] = True
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
    assert calls == ["git", "jira", "jira after"]  # jira is built on git; github isn't due
    calls.clear()
    refresh.refresh(config, db, NOW, {"github"})
    assert calls == ["github", "github after"]


def test_each_plugin_refreshes_on_its_own_setting() -> None:
    config = Config.model_validate(
        {"email": "me@x", "github": {"org": "acme"}, "git": {"repos": [], "refresh_seconds": 30}}
    )
    plugins = registry.enabled(config, Database(":memory:"), NOW)
    seconds = {e.name: refresh.refresh_seconds(e) for e in plugins}
    assert seconds == {"github": 300, "git": 30, "notes": None}


def test_a_plugin_watching_what_it_requires_isnt_refreshed_with_it_but_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Git(GitPlugin):
        def collect(self, req: CollectRequest[GitConfig]) -> LocalSnapshot:
            calls.append("git")
            return local_snapshot()

    class JiraWatchingGit(JiraPlugin):
        collects_after_graph: ClassVar[bool] = True
        requires: ClassVar[list[str]] = ["git"]
        watches: ClassVar[list[str]] = ["git"]

        def collect_after(self, req: CollectAfterRequest[JiraConfig]) -> JiraSnapshot:
            calls.append("jira")
            return JiraSnapshot(issues=[])

    swap = {"git": Git(), "jira": JiraWatchingGit()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    config = Config.model_validate(
        {"email": "me@x", "git": {"repos": []}, "jira": {"site": "https://j"}}
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW, {"git"})
    assert calls == ["git"]  # its change check decides, not the refresh
    refresh.refresh(config, db, NOW, {"jira"})
    assert calls == ["git", "jira"]
    seconds = {e.name: refresh.refresh_seconds(e) for e in registry.enabled(config, db, NOW)}
    assert seconds["jira"] == 900  # collecting after the graph, on its own setting


def test_an_item_acted_on_elsewhere_hides_until_its_plugin_has_fetched_again() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    assert item.pending is not None
    fields = json.loads(item.pending)
    assert fields["plugin"] == "github"
    assert refresh.run_action(config, db, "act_now/pending", fields).startswith("hidden")
    assert refresh.build_dashboard(config, db, NOW).act_now() == []

    SnapshotRepository(db).save_fetched("github", datetime.now(UTC) + timedelta(seconds=1))
    assert [i.title for i in refresh.build_dashboard(config, db, NOW).act_now()] == [item.title]


def test_an_item_acted_on_elsewhere_comes_back_by_itself_if_its_plugin_never_fetches() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    refresh.run_action(config, db, "act_now/pending", json.loads(item.pending or "{}"))
    later = datetime.now(UTC) + PENDING + timedelta(seconds=1)
    assert [i.title for i in refresh.build_dashboard(config, db, later).act_now()] == [item.title]


def test_acting_elsewhere_keeps_a_pin_and_a_dismissal() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    (item,) = refresh.build_dashboard(config, db, NOW).act_now()
    pin = next(a for a in item.actions if a.label == "Pin")
    refresh.run_action(config, db, pin.name, pin.fields)
    refresh.run_action(config, db, "act_now/pending", json.loads(item.pending or "{}"))
    (said,) = FeedbackRepository(db).all().values()
    assert said.action == "pin"


def test_graph_events_compare_with_what_was_stored_before_the_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a restart too: what was stored when the dashboard stopped is what the first
    refresh compares with, so a branch that became deletable meanwhile is said once."""
    local = local_snapshot(branches=[local_branch("me/done", upstream="origin/me/done")])
    open_pr = pr(9, head="me/done")
    merged = pr(9, state="MERGED", merged_at=NOW - timedelta(days=1), head="me/done")
    _, db = stored(github=snapshot(mine=[open_pr]), local=local)
    config = Config.model_validate(
        {
            "email": "me@acme.com",
            "teams": ["core"],
            "github": {"org": "acme"},
            "git": {"repos": []},
            "notifications": {"subscribe": ["git.branch_deletable"]},
        }
    )

    def github_now(self: GitHubPlugin, req: CollectRequest[GitHubConfig]) -> GitHubSnapshot:
        return snapshot(merged_recent=[merged])

    def git_now(self: GitPlugin, req: CollectRequest[GitConfig]) -> LocalSnapshot:
        return local

    def nothing_after(self: GitHubPlugin, req: AfterCollectRequest[GitHubConfig]) -> None:
        return None

    monkeypatch.setattr(GitHubPlugin, "collect", github_now)
    monkeypatch.setattr(_Git, "collect", git_now)  # this module's git
    monkeypatch.setattr(GitHubPlugin, "after_collect", nothing_after)
    refresh.refresh(config, db, NOW)
    told = NotificationRepository(db).take(NOW, timedelta(hours=1))
    assert [n.title for n in told] == ["me/done: PR merged 1d ago"]
    refresh.refresh(config, db, NOW)
    assert NotificationRepository(db).take(NOW, timedelta(hours=1)) == []  # said once


def test_a_plugin_with_more_to_collect_is_collected_again_in_one_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pieces: list[int] = []

    def in_pieces(self: GitPlugin, req: CollectRequest[GitConfig]) -> Collected[LocalSnapshot]:
        pieces.append(len(pieces))
        return Collected(local_snapshot(), more=len(pieces) < 3)

    monkeypatch.setattr(_Git, "collect", in_pieces)  # this module's git
    config = Config.model_validate({"email": "me@x", "git": {"repos": []}})
    db = Database(":memory:")
    registry.migrate(config, db)
    refresh.refresh(config, db, NOW)
    assert pieces == [0, 1, 2]
    assert SnapshotRepository(db).all()["git"].error is None
