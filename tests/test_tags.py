"""`#tags`: what carries one is related to it, and what names it points at it."""

import pytest
from conftest import NOW, pr, snapshot, stored

from move_forward import refresh, tags
from move_forward.graph import Entity, Graph, KindSpec, Ref
from move_forward.kinds import TAG, Family, Person, declaring, is_url
from move_forward.registry import enabled


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
