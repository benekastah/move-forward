"""Each family's ids are unique whoever publishes them (PRs' and tickets' URLs, people's
`<host>/<login>`), and what was kept by a handle (a ticket's key) gets its entity's id."""

from datetime import timedelta

import pytest
from conftest import ME, NOW, issue, pr, snapshot, stored

from move_forward import priority, registry
from move_forward.act_now import Feedback
from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref
from move_forward.kinds import FAMILIES, PERSON, TICKET, fits
from move_forward.plugin import GraphRequest, SnapshotRequest
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.plugins.notes.models import Entry, IcedItem, Link, Todo
from move_forward.plugins.notes.plugin import NotesPlugin
from move_forward.plugins.notes.repository import NotesRepository
from move_forward.refresh import stored_graph
from move_forward.repositories import FeedbackRepository, PriorityRepository

APP_1 = "https://jira/browse/APP-1"
"""APP-1's id: its URL on the configured site."""


def _ticket(id: str, handle: str) -> Entity:
    return Entity(ref=Ref(kind=TICKET, id=id), title=handle, attrs={"handle": handle})


def test_ids_fit_their_familys_rule() -> None:
    assert fits(TICKET, APP_1) and not fits(TICKET, "APP-1")
    assert fits(PERSON, "github.com/alice") and fits(PERSON, "git.acme.com:8443/alice")
    assert not fits(PERSON, "alice") and not fits(PERSON, "https://github.com/alice")
    assert fits("git.branch", "/src/w\tme/x")  # a plugin's own kind: any id


class _ByKey(JiraPlugin):
    """Jira, publishing its tickets by key, as it did before tickets' ids were URLs."""

    def entities(self, req: SnapshotRequest[JiraConfig, JiraSnapshot | None]) -> list[Entity]:
        return [
            e.model_copy(update={"ref": Ref(kind=TICKET, id=str(e.attrs["handle"]))})
            for e in super().entities(req)
        ]


def test_an_entity_whose_id_breaks_its_familys_rule_is_left_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config, db = stored(github=snapshot(), jira=JiraSnapshot(issues=[issue("APP-1")]))
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    assert [e.ref.id for e in graph.of_kind(TICKET)] == [APP_1]
    assert [e.ref.id for e in graph.of_kind(PERSON)] == [f"github.com/{ME}"]
    plugins = [_ByKey() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.of_kind(TICKET) == []
    assert "jira published ticket APP-1, an id its family doesn't take" in caplog.text


def test_identify_finds_a_handles_entity_else_asks_its_plugin() -> None:
    def url_for(kind: str, id: str) -> str | None:
        return f"https://jira/browse/{id}" if kind == TICKET and "/" not in id else None

    shared = [
        _ticket("https://jira/browse/OPS-1", "OPS-1"),
        _ticket("https://linear/OPS-1", "OPS-1"),
    ]
    graph = Graph([_ticket(APP_1, "APP-1"), *shared], url_for=url_for)
    assert graph.identify(TICKET, "APP-1") == APP_1  # in the graph
    assert graph.identify(TICKET, "APP-9") == "https://jira/browse/APP-9"  # from jira, by key
    assert graph.identify(TICKET, "OPS-1") == "https://jira/browse/OPS-1"  # two have it: jira's
    assert graph.identify(TICKET, APP_1) == APP_1  # fits already
    assert graph.identify(PERSON, "alice") == "alice"  # nothing knows better: kept
    assert Graph([]).identify(TICKET, "APP-1") == "APP-1"  # no jira


def test_a_handle_comes_from_the_entity_else_its_plugin_else_the_id() -> None:
    def handle_for(kind: str, id: str) -> str | None:
        return id.rsplit("/", 1)[1] if id.startswith("https://jira/browse/") else None

    graph = Graph([_ticket(APP_1, "APP-1")], handle_for=handle_for)
    assert graph.handle(TICKET, APP_1) == "APP-1"
    assert graph.handle(TICKET, "https://jira/browse/APP-9") == "APP-9"  # long done: not in it
    assert graph.handle(TICKET, "https://linear/OPS-1") == "https://linear/OPS-1"


def test_a_key_no_ticket_in_the_graph_has_is_named_by_its_url() -> None:
    config, db = stored(jira=JiraSnapshot(issues=[issue("APP-1")]))
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.named("APP-1, then APP-7") == [
        Ref(kind=TICKET, id=APP_1),
        Ref(kind=TICKET, id="https://jira/browse/APP-7"),
    ]
    assert graph.handle(TICKET, "https://jira/browse/APP-7") == "APP-7"  # jira's handle_for


def test_mfd_priority_finds_a_ticket_by_its_key() -> None:
    config, db = stored(jira=JiraSnapshot(issues=[issue("APP-1")]))
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    assert priority.resolve(graph, "APP-1") == Ref(kind=TICKET, id=APP_1)
    assert priority.resolve(graph, APP_1) == Ref(kind=TICKET, id=APP_1)


def test_core_gives_what_it_kept_by_a_tickets_key_its_url_and_keeps_what_it_cant() -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    graph, _ = stored_graph(db, registry.enabled(config, db, NOW))
    priorities, feedback = PriorityRepository(db), FeedbackRepository(db)
    for key in ("APP-1", "APP-2"):
        priorities.set(Ref(kind=TICKET, id=key), 2, NOW)
    priorities.set(Ref(kind=TICKET, id="https://jira/browse/APP-2"), 1, NOW)  # set since
    priorities.set(Ref(kind=PERSON, id="alice"), 1, NOW)  # nothing knows alice: kept
    pin = Feedback(action="pin", fingerprint="f")
    feedback.say((TICKET, "APP-1", "Move"), pin, NOW - timedelta(hours=1))

    assert priorities.rekey(list(FAMILIES), graph.identify) == 2
    assert feedback.rekey(list(FAMILIES), graph.identify) == 1
    assert priorities.all() == {
        Ref(kind=TICKET, id=APP_1): 2,
        Ref(kind=TICKET, id="https://jira/browse/APP-2"): 1,  # the newer one stays
        Ref(kind=PERSON, id="alice"): 1,
    }
    assert list(feedback.all()) == [(TICKET, APP_1, "Move")]
    assert priorities.rekey(list(FAMILIES), graph.identify) == 0  # done once


def test_notes_gives_its_links_and_ice_their_tickets_urls() -> None:
    entry = Entry(
        id=0,
        title="APP-1 Fix",
        links=[Link(kind=TICKET, ref="APP-1"), Link(kind=TICKET, ref=APP_1)],
        todos=[Todo(id=0, text="x", links=[Link(kind=TICKET, ref="APP-7")])],
    )
    config, db = stored(work_items=[entry], jira=JiraSnapshot(issues=[issue("APP-1")]))
    notes = NotesRepository(db)
    notes.add_ice("APP-1", NOW.date(), None, [IcedItem(kind=TICKET, ref_id="APP-1", name="APP-1")])
    plugins = registry.enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    [ctx] = [e.ctx for e in plugins if e.name == "notes"]
    NotesPlugin().after_collect(GraphRequest(ctx, graph))
    [kept] = notes.entries()
    assert kept.links == [Link(kind=TICKET, ref=APP_1)]  # one row: the key's went
    assert kept.todos[0].links == [Link(kind=TICKET, ref="https://jira/browse/APP-7")]
    assert [i.ref_id for ice in notes.ices() for i in ice.items] == [APP_1]


def test_without_jira_kept_ticket_keys_stay_as_they_are() -> None:
    db = Database(":memory:")
    PriorityRepository(db).set(Ref(kind=TICKET, id="APP-1"), 2, NOW)
    assert PriorityRepository(db).rekey(list(FAMILIES), Graph([]).identify) == 0
    assert PriorityRepository(db).all() == {Ref(kind=TICKET, id="APP-1"): 2}
