"""`#tags` in any entity's text: core relates what carries one, and links what names it."""

import pytest
from conftest import NOW, pr, snapshot, stored

from move_forward import refresh, tags
from move_forward.graph import Entity, Graph, Ref
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
    ],
)
def test_tags_are_hashed_words(text: str, found: list[str]) -> None:
    assert tags.tags(text) == found


def test_text_names_a_tag_however_it_spaces_hyphenates_or_cases_it() -> None:
    found = tags.pattern(["garbage", "garbage-day"])
    assert found is not None
    graph = Graph(linking={TAG: found}, handle_named=tags.handle_named)
    assert graph.named("GARBAGE  DAY moved; garbage_day; garbage") == [
        Ref(kind=TAG, id="garbage-day"),  # the longer tag, not `garbage`
        Ref(kind=TAG, id="garbage"),
    ]
    assert graph.named("garbageday, garbages") == []
    assert tags.pattern([]) is None


def test_a_tag_points_at_what_carries_it_and_is_open_while_one_is() -> None:
    open_one = Entity(ref=Ref(kind="x.a", id="1"), title="a", mentions=["#chores and #chores"])
    done = Entity(ref=Ref(kind="x.a", id="2"), title="b", mentions=["#Chores"], lifecycle="done")
    only_done = Entity(ref=Ref(kind="x.a", id="3"), title="c", mentions=["#old"], lifecycle="done")
    found = {t.ref.id: t for t in tags.entities([open_one, done, only_done])}
    assert found["chores"].refs == [open_one.ref, done.ref]
    assert (found["chores"].title, found["chores"].lifecycle) == ("#chores", "open")
    assert found["old"].lifecycle == "done"


def test_prs_sharing_a_tag_or_naming_it_are_related() -> None:
    config, db = stored(
        github=snapshot(
            mine=[
                pr(1, title="#garbage-day prep"),
                pr(2, title="Garbage Day fix"),
                pr(3, title="Other"),
            ]
        ),
    )
    graph, _ = refresh.stored_graph(db, enabled(config, db, NOW))
    first, second, third = (
        Ref(kind="pull_request", id=f"https://github.com/acme/widgets/pull/{n}") for n in (1, 2, 3)
    )
    tag = Ref(kind=TAG, id="garbage-day")
    assert graph.units(first) == graph.units(second) == [tag]
    assert [e.ref for e in graph.related(second, "pull_request")] == [first]
    assert graph.units(third) == []


def test_no_plugin_declares_a_family_named_tag() -> None:
    with (
        pytest.raises(ValueError, match="core's kind"),
        declaring("x", [Family(TAG, "tag", Person, is_url)], keep=False),
    ):
        pass
