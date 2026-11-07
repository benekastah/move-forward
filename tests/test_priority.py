from datetime import timedelta

import pytest
from conftest import NOW, entry, pr, snapshot, stored, todo

from move_forward import act_now, priority, refresh, render
from move_forward.act_now import Nomination
from move_forward.db import Database
from move_forward.domain import KindModel
from move_forward.graph import Entity, Graph, KindSpec, Ref
from move_forward.page import Action, Item, Signals
from move_forward.repositories import PriorityRepository

PR = Ref(kind="pull_request", id="https://github.com/o/r/pull/1")
ENTRY = Ref(kind="notes.entry", id="APP-1 work")
TODO = Ref(kind="notes.todo", id="7")
TICKET = Ref(kind="ticket", id="APP-1")
LINKING: dict[str, str | None] = {"ticket": r"\bAPP-\d+\b", "notes.entry": None}
KINDS = {
    "pull_request": KindSpec(KindModel, fetchable=True, resolvable=True),
    "ticket": KindSpec(KindModel, fetchable=True, resolvable=True),
    "notes.entry": KindSpec(KindModel, gone_means_done=True, resolvable=True),
    "notes.todo": KindSpec(KindModel, gone_means_done=True),
    "git.branch": KindSpec(KindModel, resolvable=True),
}
"""As the built-in plugins declare them."""


def _graph(*entities: Entity) -> Graph:
    return Graph(list(entities), linking=LINKING, kinds=KINDS)


def _items(nominations: list[Nomination], priority_of: act_now.PriorityOf) -> list[Item]:
    panels = act_now.panels(nominations, priority_of=priority_of, now=NOW)
    return sorted((i for p in panels for i in p.items), key=lambda i: -i.rank)


def _nomination(title: str, ref: Ref | None = None, **signals: object) -> Nomination:
    subjects = [ref] if ref else []
    validated = Signals.model_validate({"task": "do_it", **signals})
    return Nomination("github", Item(title=title, subjects=subjects, signals=validated))


def test_a_priority_replaces_the_plugins_importance() -> None:
    nominations = [
        _nomination("normal", Ref(kind="pull_request", id="n")),
        _nomination("raised", PR),
        _nomination("lowered", Ref(kind="pull_request", id="l"), importance=2),
    ]
    marks = {PR: 2, Ref(kind="pull_request", id="l"): 0}
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
    assert priority.level_of({TICKET: 1}, graph)(Ref(kind="pull_request", id="other")) is None


def test_items_keep_all_three_importance_choices_and_highlight_the_selected_one() -> None:
    ref = Ref(kind="pull_request", id="p")
    unmarked = _items([_nomination("x", ref)], priority.level_of({}, _graph()))[0]
    labels = [a.label for a in unmarked.actions]
    assert labels[-3:] == ["Not important", "Important", "Critical"]
    marked = _items([_nomination("x", ref)], priority.level_of({ref: 1}, _graph()))[0]
    assert [a.label for a in marked.actions][-4:] == [
        "Not important",
        "Important",
        "Critical",
        "Clear priority",
    ]
    assert [a.label for a in marked.actions if a.selected] == ["Important"]
    assert [a.icon for a in marked.actions if a.group == "Importance"] == ["❕", "❗️", "‼️"]
    html = render.item_html(marked)
    assert "<div class='button-group' role='group' aria-label='Act now'>" in html
    assert [a.icon for a in marked.actions[:3]] == ["💤", "👋", "📌"]
    assert "title='Snooze' aria-label='Snooze'><span aria-hidden='true'>💤</span></button>" in html
    assert (
        "title='Dismiss' aria-label='Dismiss'><span aria-hidden='true'>👋</span></button>" in html
    )
    assert "title='Pin' aria-label='Pin'><span aria-hidden='true'>📌</span></button>" in html
    assert "<div class='button-group' role='group' aria-label='Importance'>" in html
    assert (
        "class='secondary small selected' aria-pressed='true' title='Important' aria-label='Important'><span aria-hidden='true'>❗️</span></button>"
        in html
    )
    assert (
        "title='Not important' aria-label='Not important'><span aria-hidden='true'>❕</span></button>"
        in html
    )
    assert (
        "title='Critical' aria-label='Critical'><span aria-hidden='true'>‼️</span></button>" in html
    )
    assert marked.actions[-1].fields == {"kind": "pull_request", "id": "p"}


def test_named_buttons_join_even_with_other_actions_between_them() -> None:
    actions = [
        Action(name="snooze", label="Snooze", fields={}, group="Act now", icon="💤"),
        Action(name="priority", label="Important", fields={}),
        Action(name="ice", label="Put on ice", fields={}, group="Act now", icon="🧊"),
    ]
    html = render.buttons(actions)
    group = html.split("aria-label='Act now'>", 1)[1].split("</div>", 1)[0]
    assert "title='Snooze'" in group and "title='Put on ice'" in group
    assert group.index("title='Snooze'") < group.index("title='Put on ice'")
    assert html.index("aria-label='Act now'") < html.index(">Important</button>")


def test_act_now_groups_ice_with_snooze_dismiss_and_pin() -> None:
    config, db = stored(work_items=[entry("APP-1 finish", [todo("reply", "quick")])])
    dashboard = refresh.build_dashboard(config, db, NOW)
    shown = next(item for item in dashboard.act_now() if "APP-1 finish" in item.title)
    group = [a for a in shown.actions if a.group == "Act now"]
    assert [a.label for a in group] == ["Snooze", "Dismiss", "Pin", "Put on ice"]
    html = render.item_html(shown)
    joined = html.split("aria-label='Act now'>", 1)[1].split("</div>", 1)[0]
    assert "title='Put on ice' aria-label='Put on ice'" in joined


def test_a_review_for_me_can_be_marked_important() -> None:
    review = pr(1, author="alice")
    config, db = stored(github=snapshot(direct_requests=[review]))
    panel = refresh.build_dashboard(config, db, NOW).find_panel("github.reviews_for_me")
    assert panel is not None
    item = panel.items[0]
    assert [a.label for a in item.actions if a.group == "Importance"] == [
        "Not important",
        "Important",
        "Critical",
    ]
    important = next(a for a in item.actions if a.label == "Important")
    assert refresh.run_action(config, db, important.name, important.fields) == "priority: important"
    shown = refresh.build_dashboard(config, db, NOW).find_panel("github.reviews_for_me")
    assert shown is not None
    assert [a.label for a in shown.items[0].actions if a.selected] == ["Important"]


def test_reviews_for_me_sort_importance_before_recency() -> None:
    old = pr(1, author="alice", updated_at=NOW - timedelta(days=3))
    recent = pr(2, author="alice", updated_at=NOW - timedelta(days=1))
    newest = pr(3, author="alice", updated_at=NOW)
    config, db = stored(github=snapshot(direct_requests=[recent, old, newest]))
    priorities = PriorityRepository(db)
    priorities.set(Ref(kind="pull_request", id=old.url), 2, NOW)
    priorities.set(Ref(kind="pull_request", id=newest.url), 0, NOW)
    shown = refresh.build_dashboard(config, db, NOW).find_panel("github.reviews_for_me")
    assert shown is not None
    assert [item.url for item in shown.items] == [old.url, recent.url, newest.url]


def test_the_priority_buttons_set_and_clear_it() -> None:
    config, db = stored(github=snapshot(direct_requests=[pr(1, author="alice")]))
    priorities = PriorityRepository(db)
    first = refresh.build_dashboard(config, db, NOW).act_now()[0]
    critical = next(a for a in first.actions if a.label == "Critical")
    assert refresh.run_action(config, db, critical.name, critical.fields) == "priority: critical"
    assert priorities.all() == {Ref(kind="pull_request", id=pr(1).url): 2}
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
    assert priority.resolve(graph, "7", "notes.todo") == TODO
    with pytest.raises(LookupError, match="no entity"):
        priority.resolve(graph, "7")


def test_finished_work_loses_its_priority() -> None:
    merged, open_pr = Ref(kind="pull_request", id="m"), Ref(kind="pull_request", id="o")
    done_ticket, open_ticket = Ref(kind="ticket", id="APP-2"), TICKET
    graph = _graph(
        Entity(ref=merged, title="m", lifecycle="done"),
        Entity(ref=open_pr, title="o"),
        Entity(ref=done_ticket, title="d", lifecycle="done"),
        Entity(ref=open_ticket, title="t"),
        Entity(ref=ENTRY, title="e"),
    )
    repo = PriorityRepository(Database(":memory:"))
    gone_todo, gone_entry = TODO, Ref(kind="notes.entry", id="gone")
    branch = Ref(kind="git.branch", id="/r\tme/x")  # git's last refresh may have failed
    for ref in (merged, open_pr, done_ticket, open_ticket, ENTRY, gone_todo, gone_entry, branch):
        repo.set(ref, 1, NOW)
    assert priority.forget_finished(repo, graph) == 4
    assert set(repo.all()) == {open_pr, open_ticket, ENTRY, branch}


def test_a_pr_the_graph_lacks_is_looked_up_and_kept_when_it_cannot_be_found() -> None:
    old, lost = Ref(kind="pull_request", id="old"), Ref(kind="pull_request", id="lost")
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
