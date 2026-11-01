import pytest
from pydantic import ValidationError

from move_forward.domain import Model
from move_forward.graph import Entity, Graph, Ref

PR = Ref(kind="pr", id="https://github.com/o/r/pull/1")
OTHER_PR = Ref(kind="pr", id="https://github.com/o/r/pull/2")
TICKET = Ref(kind="ticket", id="APP-1")
ENTRY = Ref(kind="entry", id="1")
LINKING: dict[str, str | None] = {"ticket": r"\bAPP-\d+\b", "entry": None}


def _graph() -> Graph:
    return Graph(
        [
            Entity(ref=PR, title="pr 1", url=PR.id, refs=[TICKET]),
            Entity(ref=OTHER_PR, title="pr 2", url=OTHER_PR.id),
            Entity(ref=TICKET, title="APP-1", url="https://jira/APP-1"),
            Entity(ref=ENTRY, title="APP-1 work", refs=[TICKET, OTHER_PR]),
        ],
        url_for=lambda kind, id: f"https://jira/{id}" if kind == "ticket" else None,
        linking=LINKING,
    )


def _ids(entities: list[Entity]) -> list[str]:
    return [e.ref.id for e in entities]


def test_related_goes_both_ways_and_through_linking_entities() -> None:
    graph = _graph()
    assert _ids(graph.related(ENTRY, "pr")) == [OTHER_PR.id, PR.id]  # direct, then via APP-1
    assert _ids(graph.related(PR, "entry")) == [ENTRY.id]  # via APP-1
    assert _ids(graph.related(OTHER_PR, "entry")) == [ENTRY.id]  # pointed at
    assert _ids(graph.related(TICKET, "pr")) == [PR.id, OTHER_PR.id]  # mentions it; via the entry
    assert graph.related(PR, "pr") == []  # OTHER_PR is two links away: APP-1, then the entry


def test_a_shared_ref_links_only_through_a_linking_kind() -> None:
    repo = Ref(kind="repo", id="o/r")
    prs = [
        Entity(ref=PR, title="pr 1", refs=[repo]),
        Entity(ref=OTHER_PR, title="pr 2", refs=[repo]),
    ]
    assert Graph(prs, linking=LINKING).related(PR, "pr") == []
    assert _ids(Graph(prs, linking={"repo": None}).related(PR, "pr")) == [OTHER_PR.id]


def test_mentions_point_at_the_linking_entities_they_name() -> None:
    pr = Entity(ref=PR, title="pr 1", refs=[TICKET], mentions=["me/APP-1-fix", "APP-2 and APP-1"])
    graph = Graph([pr], linking=LINKING)
    found = graph.get("pr", PR.id)
    assert found is not None
    assert found.refs == [TICKET, Ref(kind="ticket", id="APP-2")]  # once each
    assert _ids(graph.related(Ref(kind="ticket", id="APP-2"), "pr")) == [PR.id]
    unlinked = Graph([pr]).get("pr", PR.id)  # no plugin declares tickets: nothing found
    assert unlinked is not None and unlinked.refs == [TICKET]


def test_url_for_prefers_the_graph_then_asks_the_plugins() -> None:
    graph = _graph()
    assert graph.url_for("pr", PR.id) == PR.id
    assert graph.url_for("ticket", "APP-9") == "https://jira/APP-9"
    assert graph.url_for("branch", "x") is None


def test_get_and_of_kind() -> None:
    graph = _graph()
    assert graph.get("ticket", "APP-1") is not None
    assert graph.get("ticket", "APP-2") is None
    assert _ids(graph.of_kind("pr")) == [PR.id, OTHER_PR.id]


def test_the_first_entity_of_a_ref_stands() -> None:
    person = Ref(kind="person", id="alice")
    graph = Graph([Entity(ref=person, title="alice"), Entity(ref=person, title="Alice")])
    assert [e.title for e in graph.of_kind("person")] == ["alice"]


class _PrView(Model):
    id: str
    state: str | None
    head: str
    draft: bool = False


def test_a_consumer_reads_entities_through_its_own_view() -> None:
    pr = Entity(
        ref=Ref(kind="pr", id="https://pr/1"),
        title="Fix",
        state="open",
        attrs={"head": "me/x", "draft": True, "extra": 1},
    )
    graph = Graph([pr, Entity(ref=Ref(kind="pr", id="https://pr/2"), title="No head")])
    assert graph.view_one("pr", "https://pr/1", _PrView) == _PrView(
        id="https://pr/1", state="open", head="me/x", draft=True
    )
    assert graph.view_one("pr", "https://pr/9", _PrView) is None
    with pytest.raises(ValidationError, match="head"):
        graph.view("pr", _PrView)  # the second PR has no head: loud, not quiet


def test_related_entities_through_a_view() -> None:
    class Titled(Model):
        id: str
        title: str

    assert _graph().view_related(PR, "entry", Titled) == [Titled(id=ENTRY.id, title="APP-1 work")]


def test_named_finds_linking_ids_in_any_text() -> None:
    graph = Graph(linking=LINKING)
    assert graph.named("APP-2: see APP-1, and APP-2 again") == [
        Ref(kind="ticket", id="APP-2"),
        TICKET,
    ]
    assert Graph().named("APP-1") == []  # no plugin declares a pattern


def test_a_hiding_entity_hides_what_it_covers_and_what_its_linkers_link() -> None:
    ice = Ref(kind="ice", id="1")
    branch = Ref(kind="branch", id="b")

    def graph(covers: list[Ref]) -> Graph:
        return Graph(
            [
                Entity(ref=PR, title="pr 1", refs=[TICKET]),
                Entity(ref=OTHER_PR, title="pr 2"),
                Entity(ref=ENTRY, title="work", refs=[OTHER_PR]),
                Entity(ref=branch, title="b"),
                Entity(ref=ice, title="on ice", refs=covers),
            ],
            linking=LINKING,
            hiding=["ice"],
        )

    assert graph([branch]).hidden(branch) and not graph([branch]).hidden(PR)
    assert graph([TICKET]).hidden(PR)  # PR 1 mentions the iced ticket
    assert graph([ENTRY]).hidden(OTHER_PR)  # the iced entry names PR 2
    assert not graph([PR]).hidden(TICKET)  # icing a PR doesn't ice its ticket
    assert not graph([ENTRY]).hidden(ice)  # what hides is never hidden
    assert not Graph(graph([branch]).of_kind("ice")).hidden(branch)  # no kind declared hiding
