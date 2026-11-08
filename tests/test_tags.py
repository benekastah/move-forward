"""`#tags`: what carries one is related to it, and what names it points at it."""

from pathlib import Path

import pytest
from conftest import NOW, pr, snapshot, stored

from move_forward import cli, panels, refresh, tags
from move_forward.db import Database
from move_forward.graph import Entity, Graph, KindSpec, Ref
from move_forward.kinds import TAG, Family, Person, declaring, is_url
from move_forward.page import Item, ItemAdditions, Panel
from move_forward.plugin import ActionError
from move_forward.registry import enabled
from move_forward.repositories import TagRepository


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("take the #garbage out", ["garbage"]),
        ("#Garbage_Day and #garbage-day bins", ["garbage-day"]),  # one tag, however written
        ("fix #7, see a#b and &#39;", []),  # a PR's number, inside a word, an HTML entity
        ("#trailing- dash", ["trailing"]),
        ("#Störung", ["störung"]),
        ("#Störung", ["störung"]),  # decomposed, as some mail clients send it
    ],
)
def test_tags_are_hashed_words(text: str, found: list[str]) -> None:
    assert tags.tags(text) == found


def _entity(id: str, title: str, *, mentions: tuple[str, ...] = (), done: bool = False) -> Entity:
    return Entity(
        ref=Ref(kind="x.a", id=id),
        title=title,
        mentions=list(mentions),
        lifecycle="done" if done else "open",
    )


def _tagged(*entities: Entity, **specs: KindSpec) -> dict[str, Entity]:
    found = tags.tagged(list(entities), specs, {Ref(kind="x.a", id="h"): ["by-hand"]})
    return {e.ref.id: e for e in found}


def test_a_tag_points_at_what_carries_it_and_is_open_while_one_is() -> None:
    found = _tagged(
        _entity("1", "#chores and #chores"),
        _entity("2", "#Chores", done=True),
        _entity("3", "#old", done=True),
        _entity("h", "tagged by hand"),
    )
    assert found["chores"].refs == [Ref(kind="x.a", id="1"), Ref(kind="x.a", id="2")]
    assert (found["chores"].title, found["chores"].lifecycle) == ("#chores", "open")
    assert found["old"].lifecycle == "done"
    assert found["by-hand"].refs == [Ref(kind="x.a", id="h")]
    assert found["1"].refs == []  # it carries the tag, so it doesn't point at it too


def test_text_names_a_tag_however_it_spaces_hyphenates_or_cases_it() -> None:
    found = _tagged(
        _entity("1", "#garbage-day prep"),
        _entity("2", "#garbage"),
        _entity("3", "GARBAGE  DAY moved", mentions=("and garbage_day", "the garbage")),
        _entity("4", "garbageday, garbages"),  # not its words
    )
    day, garbage = Ref(kind=TAG, id="garbage-day"), Ref(kind=TAG, id="garbage")
    assert found["3"].refs == [day, garbage]  # the longer tag first, then `garbage` alone
    assert found["4"].refs == []
    assert found["2"].refs == []  # what it carries


def test_a_kind_others_write_doesnt_put_tags_on_its_entities() -> None:
    sale = Entity(ref=Ref(kind="y.mail", id="1"), title="#sale ends Friday")
    other = Entity(ref=Ref(kind="y.mail", id="2"), title="Sale!")
    found = tags.tagged([sale, other], {"y.mail": KindSpec(tags.TagAttrs, tags_from_title=False)})
    assert [e.ref for e in found] == [sale.ref, other.ref]  # no tag, so nothing names one


def test_prs_sharing_a_tag_or_naming_it_point_at_it_but_arent_linked() -> None:
    config, db = stored(
        github=snapshot(
            mine=[pr(1, title="#garbage-day prep"), pr(2, title="Garbage Day fix"), pr(3)]
        ),
    )
    graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
    first, second, third = (
        Ref(kind="pull_request", id=f"https://github.com/acme/widgets/pull/{n}") for n in (1, 2, 3)
    )
    [tag] = graph.of_kind(TAG)
    assert tag.refs == [first]
    assert graph.related(second, TAG) == [tag]
    assert graph.related(third, TAG) == []
    assert graph.units(second) == []  # not a unit of work: no ice, priority or join through it
    assert graph.related(second, "pull_request") == []


def test_no_plugin_declares_a_family_named_tag() -> None:
    with (
        pytest.raises(ValueError, match="core's kind"),
        declaring("x", [Family(TAG, "tag", Person, is_url)], keep=False),
    ):
        pass


def test_words_are_compared_case_folded_and_short_tags_need_their_hash() -> None:
    found = _tagged(
        _entity("1", "#straße #a #İstanbul #हिन्दी"),
        _entity("2", "STRASSE closed, a note, İSTANBUL trip, हिन्दी class"),
        _entity("3", "ह"),
    )
    assert found["2"].refs == [
        Ref(kind=TAG, id="strasse"),  # case-folded, as STRASSE is
        Ref(kind=TAG, id="i̇stanbul"),
        Ref(kind=TAG, id="हिन्दी"),
    ]  # not #a: too short to be named without its #
    assert found["3"].refs == []


def test_the_tags_of_a_ticket_reach_what_belongs_to_it() -> None:
    ticket = Entity(ref=Ref(kind="ticket", id="https://jira/APP-1"), title="#chores sink")
    work = Entity(ref=Ref(kind="x.pr", id="1"), title="fix", refs=[ticket.ref])
    graph = Graph(tags.tagged([ticket, work], {}), linking={"ticket": None})
    assert [t.ref for t in graph.related(work.ref, TAG)] == [Ref(kind=TAG, id="chores")]


def test_a_short_tag_is_named_with_its_hash() -> None:
    found = _tagged(
        _entity("1", "#qa thing"),
        _entity("2", "sign-off", mentions=("waiting on #QA",)),
        _entity("3", "qa sign-off"),
    )
    assert found["2"].refs == [Ref(kind=TAG, id="qa")]
    assert found["3"].refs == []


def test_a_title_that_cant_carry_a_tag_still_names_one_by_its_hash() -> None:
    carrier = _entity("1", "#qa thing")
    mail = Entity(ref=Ref(kind="y.mail", id="m"), title="#QA sign-off")
    found = tags.tagged([carrier, mail], {"y.mail": KindSpec(tags.TagAttrs, tags_from_title=False)})
    assert found[1].refs == [Ref(kind=TAG, id="qa")]


def test_a_tag_put_on_by_hand_shows_on_its_items_and_comes_off() -> None:
    config, db = stored(github=snapshot(mine=[pr(1, title="Fix it")]))
    fields = {"kind": "pull_request", "id": pr(1).url}
    assert refresh.run_action(config, db, "act_now/tag", {**fields, "input": "#Garbage Day"}) == (
        "tagged #garbage-day"
    )
    assert refresh.run_action(config, db, "act_now/tag", {**fields, "input": "garbage-day"}) == (
        "already #garbage-day"
    )
    with pytest.raises(ActionError, match="isn't a tag"):
        refresh.run_action(config, db, "act_now/tag", {**fields, "input": "#7"})
    items = [
        i
        for p in refresh.build_dashboard(config, db, NOW).panels
        for i in p.items
        if i.subjects and i.subjects[0].id == pr(1).url
    ]
    assert items
    for item in items:  # the same wherever it's shown
        assert "#garbage-day" in item.badges
        assert [a.label for a in item.actions if a.group == "Tags"] == [
            "Tag",
            "Untag #garbage-day",
        ]
    graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
    assert [t.refs for t in graph.of_kind(TAG)] == [[Ref(kind="pull_request", id=pr(1).url)]]
    untag = {**fields, "tag": "garbage-day"}
    assert refresh.run_action(config, db, "act_now/untag", untag) == "#garbage-day taken off"
    assert refresh.run_action(config, db, "act_now/untag", untag) == "#garbage-day wasn't on it"
    assert TagRepository(db).all() == {}


def test_the_tag_command_puts_a_tag_on_and_takes_it_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    db_path = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n')
    assert cli.main(["-c", str(config), "notes", "add-entry", "Chores"]) == 0
    assert cli.main(["-c", str(config), "tag", "Chores", "#Garbage_Day"]) == 0
    assert cli.main(["-c", str(config), "tag", "Chores", "#7"]) == 1
    assert cli.main(["-c", str(config), "tag", "Nothing", "x"]) == 1
    entry = Ref(kind="notes.entry", id="Chores")
    assert TagRepository(Database(db_path)).all() == {entry: ["garbage-day"]}
    assert cli.main(["-c", str(config), "tag", "Chores", "garbage-day", "--remove"]) == 0
    assert TagRepository(Database(db_path)).all() == {}
    assert "Chores: #garbage-day" in capsys.readouterr().out


def test_tags_on_gone_entities_are_forgotten_and_finished_work_keeps_them() -> None:
    config, db = stored(
        github=snapshot(mine=[pr(1)], merged=[pr(2, merged_at="2026-10-29T00:00:00Z")]),
    )
    gone_entry = Ref(kind="notes.entry", id="Chores")  # deleted: a new "Chores" starts clean
    merged = Ref(kind="pull_request", id=pr(2).url)
    repo = TagRepository(db)
    for ref in (gone_entry, merged):
        repo.add(ref, "chores", NOW)
    plugins = enabled(config, db, NOW)
    graph, _ = refresh.stored_graph(db, plugins)
    notices = refresh._prune_plugins(plugins, graph, db, NOW, 30)  # pyright: ignore[reportPrivateUsage]
    assert "tags: took my tags off 1 entity(ies) that are gone" in notices
    assert repo.all() == {merged: ["chores"]}


def test_untag_takes_the_tag_however_its_written() -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]))
    fields = {"kind": "pull_request", "id": pr(1).url}
    refresh.run_action(config, db, "act_now/tag", {**fields, "input": "garbage-day"})
    untag = {**fields, "tag": "#Garbage Day"}
    assert refresh.run_action(config, db, "act_now/untag", untag) == "#garbage-day taken off"


def test_ice_gets_no_tags_and_focus_parts_do() -> None:
    graph = Graph(hiding=["notes.iced"])
    tagging = tags.Tags(graph, {})
    assert tagging.additions(Item(title="iced", subjects=[Ref(kind="notes.iced", id="1")])) == (
        ItemAdditions()
    )
    part = Ref(kind="notes.todo", id="7")
    stage = Panel(
        key="stage",
        title="Stage",
        description="",
        items=[
            Item(
                title="Entry",
                subjects=[Ref(kind="notes.entry", id="e")],
                parts=[Item(title="Todo", subjects=[part])],
            )
        ],
    )
    built = panels.build(
        NOW,
        [panels.PluginPanels("x", "X", [stage])],
        {},
        item_actions=lambda item, owner, surface: tagging.additions(item),
    )
    focused = built.focus_items("x.stage")
    assert focused is not None
    [(_, todo)] = focused
    assert [a.fields for a in todo.actions if a.label == "Tag"] == [
        {"kind": part.kind, "id": part.id}
    ]


def test_forget_gone_keeps_tags_on_done_work_in_the_graph() -> None:
    merged, gone = Ref(kind="pull_request", id="merged"), Ref(kind="notes.todo", id="9")
    graph = Graph(
        [Entity(ref=merged, title="m", lifecycle="done")],
        kinds={
            "pull_request": KindSpec(tags.TagAttrs),
            "notes.todo": KindSpec(tags.TagAttrs, gone_means_done=True),
        },
    )
    repo = TagRepository(Database(":memory:"))
    for ref in (merged, gone):
        repo.add(ref, "chores", NOW)
    assert tags.forget_gone(repo, graph) == 1
    assert repo.all() == {merged: ["chores"]}
