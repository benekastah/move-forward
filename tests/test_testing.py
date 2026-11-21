"""`move_forward.testing`: a plugin's tests run as core runs the plugin."""

from typing import Any, ClassVar

import pytest

from move_forward.api import (
    API_VERSION,
    ActionHandler,
    ActionRequest,
    Collector,
    ContextRequest,
    Entity,
    Item,
    KindModel,
    KindSpec,
    KvRepository,
    Model,
    Notify,
    PageHandler,
    PageRequest,
    Panel,
    Plugin,
    Ref,
    RefreshRequest,
    Request,
    Signals,
    attrs,
    html,
)
from move_forward.api.kinds import PULL_REQUEST, TICKET, PullRequest, Ticket
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


class _Issues:
    """Linear's issues, kept in its kv as JSON."""

    def __init__(self, kv: KvRepository):
        self.kv = kv

    def save(self, snapshot: LinearSnapshot) -> None:
        self.kv.set("snapshot", snapshot.model_dump_json())

    def load(self) -> LinearSnapshot | None:
        kept = self.kv.get("snapshot")
        return LinearSnapshot.model_validate_json(kept) if kept else None


class LinearPlugin(Plugin[LinearConfig, LinearSnapshot], Collector[LinearConfig, LinearSnapshot]):
    """A plugin of its own: its issues, by key, kept in kv."""

    name: ClassVar[str] = "linear"
    title: ClassVar[str] = "Linear"
    api_version = API_VERSION
    config_model = LinearConfig
    kinds: ClassVar[dict[str, KindSpec]] = {ISSUE: KindSpec(IssueAttrs)}
    notification_kinds: ClassVar[dict[str, str]] = {"to_triage": "an issue is to triage"}

    def collect(self, req: RefreshRequest[LinearConfig]) -> LinearSnapshot:
        """No Linear to ask: the tests `store` what it would have fetched."""
        return LinearSnapshot(issues=[])

    def snapshots(self, req: ContextRequest[LinearConfig]) -> _Issues:
        return _Issues(req.ctx.kv)

    def entities(self, req: Request[LinearConfig, LinearSnapshot]) -> list[Entity]:
        issues = req.snapshot.issues if req.snapshot else []
        team = req.ctx.config.team
        return [
            Entity(ref=Ref(kind=ISSUE, id=i.key), title=i.title, attrs=attrs(IssueAttrs(team=team)))
            for i in issues
        ]

    def panels(self, req: Request[LinearConfig, LinearSnapshot]) -> list[Panel]:
        issues = req.snapshot.issues if req.snapshot else []

        def item(issue: Issue) -> Item:
            prs = req.graph.view_one(PULL_REQUEST, issue.pr, _PrView) if issue.pr else None
            return Item(
                title=f"{issue.key} {issue.title}",
                badges=[f"PR {prs.handle}"] if prs else [],
                subjects=[Ref(kind=ISSUE, id=issue.key)],
                notify=Notify(kind="to_triage", text="to triage"),
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
        def issue(req: PageRequest[LinearConfig]) -> html.Html | None:
            key = req.page.query.get("key", [""])[0]
            return html.html(t"<p>{key} at {req.page.path}</p>") if key else None

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


def test_events_need_a_store_first() -> None:
    linear = Harness(LinearPlugin(), {"team": "ENG"})
    with pytest.raises(RuntimeError, match="store a snapshot first"):
        linear.events()
    linear.store(LinearSnapshot(issues=[]))
    assert linear.events() == []


def test_events_are_those_of_the_last_store_and_include_items_newly_shown() -> None:
    linear = Harness(LinearPlugin(), {"team": "ENG"})
    linear.store(LinearSnapshot(issues=[Issue(key="ENG-1", title="Fix it")]))
    assert linear.events() == []  # the first: nothing to compare with
    two = [Issue(key="ENG-1", title="Fix it"), Issue(key="ENG-2", title="Ship it")]
    linear.store(LinearSnapshot(issues=two))
    assert [(e.kind, e.thing, e.title) for e in linear.events()] == [
        ("to_triage", "ENG-2", "ENG-2 Ship it")
    ]
    assert [e.thing for e in linear.events()] == ["ENG-2"]  # asking again changes nothing


def _config_error(section: dict[str, Any]) -> None:
    Harness(LinearPlugin(), section)


def test_a_bad_section_fails_as_the_config_would() -> None:
    with pytest.raises(ValueError, match=r"linear\.team"):
        _config_error({})


class TrackerTicket(Ticket):
    team: str


class TrackerPlugin(Plugin[LinearConfig, LinearSnapshot]):
    """A plugin publishing tickets, a family core declares."""

    name: ClassVar[str] = "tracker"
    title: ClassVar[str] = "Tracker"
    api_version = API_VERSION
    config_model = LinearConfig
    kinds: ClassVar[dict[str, KindSpec]] = {TICKET: KindSpec(TrackerTicket)}

    def entities(self, req: Request[LinearConfig, LinearSnapshot]) -> list[Entity]:
        ticket = TrackerTicket(handle="T-1", summary="Big", team="ENG")
        return [
            Entity(ref=Ref(kind=TICKET, id=id), title="Big", attrs=attrs(ticket))
            for id in ("https://tracker/T-1", "T-1")  # the second breaks the family's rule
        ]


def test_a_plugin_publishes_a_family_as_core_would() -> None:
    tracker = Harness(TrackerPlugin(), {"team": "ENG"})
    [ticket] = tracker.entities()
    assert (ticket.ref.id, ticket.kind, ticket.family) == (
        "https://tracker/T-1",
        "tracker.ticket",
        "ticket",
    )


class TicketReader(LinearPlugin):
    """Reads tickets, which another plugin publishes."""

    name: ClassVar[str] = "reader"
    kinds: ClassVar[dict[str, KindSpec]] = {}


def test_a_plugin_reading_a_family_is_given_the_entities_that_fit_it() -> None:
    reader = Harness(TicketReader(), {"team": "ENG"})
    reader.with_entities(
        [
            Entity(
                ref=Ref(kind=TICKET, id=id),
                title="Big",
                attrs=attrs(Ticket(handle="T-1", summary="Big")),
            )
            for id in ("https://tracker/T-1", "T-1")
        ]
    )
    [ticket] = reader.graph().of_kind(TICKET)
    assert (ticket.ref.id, ticket.family) == ("https://tracker/T-1", TICKET)


def test_seeding_a_kind_neither_a_family_nor_a_plugins_says_so() -> None:
    reader = Harness(TicketReader(), {"team": "ENG"})
    deal = Entity(ref=Ref(kind="deal", id="https://crm/deals/1"), title="Big", attrs={})
    with pytest.raises(ValueError, match="neither a family nor a plugin's kind"):
        reader.with_entities([deal])
