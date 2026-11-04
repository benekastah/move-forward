"""`move_forward.testing`: a plugin's tests run as core runs the plugin."""

from typing import Any, ClassVar

import pytest

from move_forward.api import (
    API_VERSION,
    ActionHandler,
    ActionRequest,
    EntitiesRequest,
    Entity,
    Item,
    KindModel,
    KindSpec,
    LoadRequest,
    Model,
    PageHandler,
    PageRequest,
    Panel,
    PanelsRequest,
    Plugin,
    Ref,
    Signals,
    StoreRequest,
    attrs,
)
from move_forward.api.kinds import PULL_REQUEST, PullRequest
from move_forward.strict import Strict
from move_forward.testing import NOW, Harness

ISSUE = "linear.issue"


class LinearConfig(Strict):
    team: str


class Issue(Model):
    key: str
    title: str
    pr: str | None = None


class LinearSnapshot(Model):
    issues: list[Issue]


class IssueAttrs(KindModel):
    team: str


class _PrView(Model):
    handle: str


class LinearPlugin(Plugin[LinearConfig, LinearSnapshot]):
    """A plugin of its own: its issues, by key, kept in kv."""

    name: ClassVar[str] = "linear"
    title: ClassVar[str] = "Linear"
    api_version = API_VERSION
    config_model = LinearConfig
    snapshot_model = LinearSnapshot
    kinds: ClassVar[dict[str, KindSpec]] = {ISSUE: KindSpec(IssueAttrs)}

    def store(self, req: StoreRequest[LinearConfig, LinearSnapshot]) -> None:
        req.ctx.kv.set("snapshot", req.snapshot.model_dump_json())

    def load(self, req: LoadRequest[LinearConfig]) -> LinearSnapshot | None:
        kept = req.ctx.kv.get("snapshot")
        return LinearSnapshot.model_validate_json(kept) if kept else None

    def entities(self, req: EntitiesRequest[LinearConfig, LinearSnapshot]) -> list[Entity]:
        issues = req.snapshot.issues if req.snapshot else []
        team = req.ctx.config.team
        return [
            Entity(ref=Ref(kind=ISSUE, id=i.key), title=i.title, attrs=attrs(IssueAttrs(team=team)))
            for i in issues
        ]

    def panels(self, req: PanelsRequest[LinearConfig, LinearSnapshot]) -> list[Panel]:
        issues = req.snapshot.issues if req.snapshot else []

        def item(issue: Issue) -> Item:
            prs = req.graph.view_one(PULL_REQUEST, issue.pr, _PrView) if issue.pr else None
            return Item(
                title=f"{issue.key} {issue.title}",
                badges=[f"PR {prs.handle}"] if prs else [],
                subjects=[Ref(kind=ISSUE, id=issue.key)],
            )

        return [
            Panel(
                key="triage",
                title="Triage",
                description="",
                items=[item(i) for i in issues],
                act_now=True,
                signals=Signals(verb="Triage", task="triage"),
            )
        ]

    def actions(self) -> dict[str, ActionHandler]:
        def close(req: ActionRequest[LinearConfig]) -> str:
            return f"closed {req.field('key')}"

        return {"close": close}

    def pages(self) -> dict[str, PageHandler]:
        def issue(req: PageRequest[LinearConfig]) -> str | None:
            key = req.query.get("key", [""])[0]
            return f"<p>{key} at {req.path}</p>" if key else None

        return {"issue": issue}


def _pr(url: str) -> Entity:
    model = PullRequest(
        handle="#7",
        repo="acme/w",
        head="eng-1",
        draft=False,
        mine=True,
        changes_requested=False,
        created_at=NOW.isoformat(),
        merged_at=None,
    )
    return Entity(ref=Ref(kind=PULL_REQUEST, id=url), title="Fix", attrs=attrs(model))


def _harness() -> Harness:
    linear = Harness(LinearPlugin(), {"team": "ENG"})
    linear.store(LinearSnapshot(issues=[Issue(key="ENG-1", title="Fix it", pr="https://pr/7")]))
    return linear


def test_a_plugin_publishes_and_shows_its_panels_as_core_builds_them() -> None:
    linear = _harness()
    [issue] = linear.entities()
    assert (issue.ref, issue.attrs) == (Ref(kind=ISSUE, id="ENG-1"), {"team": "ENG"})
    [triage] = linear.panels()
    assert [i.title for i in triage.items] == ["ENG-1 Fix it"]
    assert [i.title for i in linear.act_now()] == ["Triage: ENG-1 Fix it"]
    assert "ENG-1 Fix it" in linear.render_item(triage.items[0])


def test_other_plugins_entities_are_seeded() -> None:
    linear = _harness().with_entities([_pr("https://pr/7")])
    [triage] = linear.panels()
    assert triage.items[0].badges == ["PR #7"]
    assert linear.graph().get(PULL_REQUEST, "https://pr/7") is not None


def test_its_actions_and_pages_run_as_the_server_runs_them() -> None:
    linear = _harness()
    assert linear.run_action("close", {"key": "ENG-1"}) == "closed ENG-1"
    assert linear.page("issue", {"key": ["ENG-1"]}) == "<p>ENG-1 at /linear/issue</p>"
    assert linear.page("issue") is None


def test_the_clock_moves_and_core_is_left_as_it_was() -> None:
    from move_forward import registry

    before = list(registry.PLUGINS)
    linear = _harness()
    linear.tick(3600)
    assert linear.ctx.now == NOW.replace(hour=13)
    assert before == registry.PLUGINS


def test_graph_events_need_a_store_first() -> None:
    linear = Harness(LinearPlugin(), {"team": "ENG"})
    with pytest.raises(RuntimeError, match="store a snapshot first"):
        linear.graph_events()
    linear.store(LinearSnapshot(issues=[]))
    assert linear.graph_events() == []


def _config_error(section: dict[str, Any]) -> None:
    Harness(LinearPlugin(), section)


def test_a_bad_section_fails_as_the_config_would() -> None:
    with pytest.raises(ValueError, match=r"linear\.team"):
        _config_error({})
