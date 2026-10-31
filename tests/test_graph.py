from move_forward_dashboard.graph import Entity, Graph, Ref

PR = Ref(kind="pr", id="https://github.com/o/r/pull/1")
OTHER_PR = Ref(kind="pr", id="https://github.com/o/r/pull/2")
TICKET = Ref(kind="ticket", id="APP-1")
ENTRY = Ref(kind="entry", id="1")


def _graph() -> Graph:
    return Graph(
        [
            Entity(ref=PR, title="pr 1", url=PR.id, refs=[TICKET]),
            Entity(ref=OTHER_PR, title="pr 2", url=OTHER_PR.id),
            Entity(ref=TICKET, title="APP-1", url="https://jira/APP-1"),
            Entity(ref=ENTRY, title="APP-1 work", refs=[TICKET, OTHER_PR]),
        ],
        url_for=lambda kind, id: f"https://jira/{id}" if kind == "ticket" else None,
    )


def _ids(entities: list[Entity]) -> list[str]:
    return [e.ref.id for e in entities]


def test_related_goes_both_ways_and_through_a_shared_ticket() -> None:
    graph = _graph()
    assert _ids(graph.related(ENTRY, "pr")) == [OTHER_PR.id, PR.id]  # direct, then via APP-1
    assert _ids(graph.related(PR, "entry")) == [ENTRY.id]  # via APP-1
    assert _ids(graph.related(OTHER_PR, "entry")) == [ENTRY.id]  # pointed at
    assert _ids(graph.related(TICKET, "pr")) == [PR.id]
    assert graph.related(PR, "pr") == []  # OTHER_PR is two hops away, through the entry


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
