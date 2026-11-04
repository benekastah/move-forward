"""Families of kinds, modeled in core: a plugin's kind named for one extends its model, and
other plugins read every publisher's entities of it through that model."""

from typing import Any, ClassVar, cast

import pytest
from conftest import NOW, issue, pr, snapshot, stored

from move_forward import registry
from move_forward.domain import Model
from move_forward.graph import Entity, Graph, KindSpec, Ref, attrs
from move_forward.kinds import PULL_REQUEST, TICKET, PullRequest, Ticket
from move_forward.plugin import EntitiesRequest, Plugin
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.refresh import stored_graph


class _OwnTicket(Model):
    summary: str


def _plugin(name: str, kinds: dict[str, KindSpec]) -> Plugin[Any, Any]:
    """A plugin publishing `kinds`, as `name`."""
    return cast(Plugin[Any, Any], type("Publishing", (Plugin,), {"name": name, "kinds": kinds})())


def test_a_kind_named_for_a_family_must_extend_its_model() -> None:
    class LinearTicket(Ticket):
        team: str

    registry.check_plugins([_plugin("linear", {TICKET: KindSpec(LinearTicket)})])
    with pytest.raises(RuntimeError, match="linear's ticket must extend core's Ticket"):
        registry.check_plugins([_plugin("linear", {TICKET: KindSpec(_OwnTicket)})])


def test_a_plugins_other_kinds_are_named_for_it() -> None:
    registry.check_plugins([_plugin("linear", {"linear.cycle": KindSpec(Model)})])
    for kind in ("cycle", "github.cycle", "linear.", "linear.Cycle"):
        with pytest.raises(RuntimeError, match=f"linear's kind '{kind}' must be named linear"):
            registry.check_plugins([_plugin("linear", {kind: KindSpec(Model)})])


def test_the_built_in_kinds_extend_their_families() -> None:
    registry.check_plugins(registry.PLUGINS)
    github = next(p for p in registry.PLUGINS if p.name == "github")
    assert issubclass(github.kinds[PULL_REQUEST].attrs, PullRequest)


def test_text_names_a_ticket_by_its_handle_and_a_shared_key_names_both() -> None:
    def ticket(id: str, handle: str) -> Entity:
        return Entity(ref=Ref(kind=TICKET, id=id), title=handle, attrs={"handle": handle})

    jira = ticket("https://jira/browse/OPS-1", "OPS-1")
    linear = ticket("https://linear/OPS-1", "OPS-1")
    pr = Entity(
        ref=Ref(kind=PULL_REQUEST, id="https://pr/1"), title="Fix OPS-1", mentions=["OPS-1"]
    )
    graph = Graph([jira, linear, pr], linking={TICKET: r"\bOPS-\d+\b"})
    assert [e.ref.id for e in graph.by_handle(TICKET, "OPS-1")] == [jira.ref.id, linear.ref.id]
    linked = graph.get(PULL_REQUEST, "https://pr/1")
    assert linked is not None and set(linked.refs) == {jira.ref, linear.ref}
    assert graph.named("about OPS-1") == [jira.ref, linear.ref]
    assert graph.named("about OPS-9") == [Ref(kind=TICKET, id="OPS-9")]  # none has it yet


def test_core_marks_each_entity_with_its_family() -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]))
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    found = graph.get(PULL_REQUEST, pr(1).url)
    assert found is not None and found.family == PULL_REQUEST
    assert found.kind == "github.pull_request"
    assert found.attrs["handle"] == "#1"
    assert graph.of_kind("github.pull_request") == [found]


class _GitLab(JiraPlugin):
    """Jira, also publishing pull requests as another tool would: one of its own, and one
    github publishes too."""

    kinds: ClassVar[dict[str, KindSpec]] = {**JiraPlugin.kinds, PULL_REQUEST: KindSpec(PullRequest)}

    def entities(self, req: EntitiesRequest[JiraConfig, JiraSnapshot]) -> list[Entity]:
        def merge_request(url: str) -> Entity:
            model = PullRequest(
                handle="!2",
                repo="acme/widgets",
                head="me/mr",
                draft=False,
                mine=True,
                changes_requested=False,
                created_at=NOW.isoformat(),
                merged_at=None,
            )
            return Entity(ref=Ref(kind=PULL_REQUEST, id=url), title=url, attrs=attrs(model))

        own = merge_request("https://gitlab/acme/widgets/-/merge_requests/2")
        return [*super().entities(req), own, merge_request(pr(1).url)]


def test_two_plugins_publish_one_family_and_a_shared_id_is_the_first_ones(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    plugins = [_GitLab() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    mr = "https://gitlab/acme/widgets/-/merge_requests/2"
    assert [e.ref.id for e in graph.of_kind(PULL_REQUEST)] == [pr(1).url, mr]
    assert [e.ref.id for e in graph.of_kind("jira.pull_request")] == [mr]
    assert [e.ref.id for e in graph.of_kind("github.pull_request")] == [pr(1).url]
    assert f"jira published pull_request {pr(1).url} twice" in caplog.text
