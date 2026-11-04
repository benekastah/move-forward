from datetime import timedelta

import pytest
from conftest import NOW, pr, snapshot, stored

from move_forward import act_now, priority, refresh
from move_forward.act_now import Nomination
from move_forward.db import Database
from move_forward.domain import Model
from move_forward.graph import Entity, Graph, KindSpec, Ref
from move_forward.page import Item, Signals
from move_forward.repositories import PriorityRepository

PR = Ref(kind="pr", id="https://github.com/o/r/pull/1")
ENTRY = Ref(kind="entry", id="APP-1 work")
TODO = Ref(kind="todo", id="7")
TICKET = Ref(kind="ticket", id="APP-1")
LINKING: dict[str, str | None] = {"ticket": r"\bAPP-\d+\b", "entry": None}
KINDS = {
    "pr": KindSpec(Model, fetchable=True, resolvable=True),
    "ticket": KindSpec(Model, fetchable=True, resolvable=True),
    "entry": KindSpec(Model, gone_means_done=True, resolvable=True),
    "todo": KindSpec(Model, gone_means_done=True),
    "branch": KindSpec(Model, resolvable=True),
}
"""As the built-in plugins declare them."""


def _graph(*entities: Entity) -> Graph:
    return Graph(list(entities), linking=LINKING, kinds=KINDS)


def _items(nominations: list[Nomination], priority_of: act_now.PriorityOf) -> list[Item]:
    panels = act_now.panels(nominations, priority_of=priority_of, now=NOW)
    return sorted((i for p in panels for i in p.items), key=lambda i: -i.rank)


def _nomination(title: str, ref: Ref | None = None, **signals: object) -> Nomination:
    subjects = [ref] if ref else []
    return Nomination("github", Item(title=title, subjects=subjects, signals=Signals(**signals)))  # pyright: ignore[reportArgumentType]


def test_a_priority_replaces_the_plugins_importance() -> None:
    nominations = [
        _nomination("normal", Ref(kind="pr", id="n")),
        _nomination("raised", PR),
        _nomination("lowered", Ref(kind="pr", id="l"), importance=2),
    ]
    marks = {PR: 2, Ref(kind="pr", id="l"): 0}
    shown = _items(nominations, priority.level_of(marks, _graph()))
    assert [i.title for i in shown] == ["raised", "normal", "lowered"]
    assert shown[0].details[0] == "why: critical"
    assert shown[2].details[0] == "why: not important"


def test_a_priority_counts_for_the_units_and_todos_around_an_entity() -> None:
    graph = _graph(
        Entity(ref=PR, title="pr", refs=[TICKET]),
        Entity(ref=TICKET, title="APP-1"),
        Entity(ref=ENTRY, title="APP-1 work", refs=[TICKET]),
        Entity(ref=TODO, title="todo", refs=[ENTRY]),
    )
    assert priority.level_of({TICKET: 1}, graph)(PR) == 1  # the PR's ticket
    assert priority.level_of({TODO: 2}, graph)(ENTRY) == 2  # the entry's todo
    assert priority.level_of({TODO: 2, TICKET: 0}, graph)(PR) == 2  # the highest, via the entry
    assert priority.level_of({TICKET: 1}, graph)(Ref(kind="pr", id="other")) is None


def test_items_get_buttons_to_set_the_other_priorities_and_to_clear_one() -> None:
    ref = Ref(kind="pr", id="p")
    unmarked = _items([_nomination("x", ref)], priority.level_of({}, _graph()))[0]
    labels = [a.label for a in unmarked.actions]
    assert labels[-3:] == ["Not important", "Important", "Critical"]
    marked = _items([_nomination("x", ref)], priority.level_of({ref: 1}, _graph()))[0]
    assert [a.label for a in marked.actions][-3:] == ["Not important", "Critical", "Clear priority"]
    assert marked.actions[-1].fields == {"kind": "pr", "id": "p"}


def test_the_priority_buttons_set_and_clear_it() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    priorities = PriorityRepository(db)
    first = refresh.build_dashboard(config, db, NOW).act_now()[0]
    critical = next(a for a in first.actions if a.label == "Critical")
    assert refresh.run_action(config, db, critical.name, critical.fields) == "priority: critical"
    assert priorities.all() == {Ref(kind="pr", id=pr(1).url): 2}
    clear = next(
        a
        for a in refresh.build_dashboard(config, db, NOW).act_now()[0].actions
        if a.label == "Clear priority"
    )
    assert refresh.run_action(config, db, clear.name, clear.fields) == "priority cleared"
    assert priorities.all() == {}
    with pytest.raises(refresh.ActionError, match="no priority 5"):
        refresh.run_action(config, db, "act_now/priority", {**critical.fields, "level": "5"})


def test_resolve_finds_the_entity_a_name_belongs_to() -> None:
    graph = _graph(Entity(ref=PR, title="pr"), Entity(ref=TODO, title="todo"))
    assert priority.resolve(graph, PR.id) == PR
    assert priority.resolve(graph, "7", "todo") == TODO
    with pytest.raises(LookupError, match="no entity"):
        priority.resolve(graph, "7")


def test_finished_work_loses_its_priority() -> None:
    merged, open_pr = Ref(kind="pr", id="m"), Ref(kind="pr", id="o")
    done_ticket, open_ticket = Ref(kind="ticket", id="APP-2"), TICKET
    graph = _graph(
        Entity(ref=merged, title="m", lifecycle="done"),
        Entity(ref=open_pr, title="o"),
        Entity(ref=done_ticket, title="d", lifecycle="done"),
        Entity(ref=open_ticket, title="t"),
        Entity(ref=ENTRY, title="e"),
    )
    repo = PriorityRepository(Database(":memory:"))
    gone_todo, gone_entry = TODO, Ref(kind="entry", id="gone")
    branch = Ref(kind="branch", id="/r\tme/x")  # git's last refresh may have failed
    for ref in (merged, open_pr, done_ticket, open_ticket, ENTRY, gone_todo, gone_entry, branch):
        repo.set(ref, 1, NOW)
    assert priority.forget_finished(repo, graph) == 4
    assert set(repo.all()) == {open_pr, open_ticket, ENTRY, branch}


def test_a_pr_the_graph_lacks_is_looked_up_and_kept_when_it_cannot_be_found() -> None:
    old, lost = Ref(kind="pr", id="old"), Ref(kind="pr", id="lost")
    graph = Graph(
        [],
        fetch=lambda kind, ids: (
            [Entity(ref=Ref(kind=kind, id="old"), title="o", lifecycle="done")]
            if "old" in ids
            else []
        ),
        kinds=KINDS,
    )
    repo = PriorityRepository(Database(":memory:"))
    repo.set(old, 2, NOW - timedelta(days=90))
    repo.set(lost, 2, NOW)
    assert priority.forget_finished(repo, graph) == 1
    assert set(repo.all()) == {lost}
