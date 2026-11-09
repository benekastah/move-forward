"""The tag view: the page filtered to what carries one of the tags in its address."""

import re

from conftest import dashboard, entry, pr, snapshot, titles, todo

from move_forward import render, tag_view
from move_forward.panels import ACT_NOW, Dashboard


def _board() -> Dashboard:
    """Three PRs, two tagged in their titles, and an entry with a tagged todo and one not."""
    return dashboard(
        github=snapshot(
            mine=[
                pr(1, title="Fix #chores #urgent"),
                pr(2, title="Bins #garbage-day"),
                pr(3, title="Other"),
            ]
        ),
        work_items=[entry("Errands", [todo("one #chores", "quick"), todo("two", "quick")])],
    )


def _hrefs(html: str, cls: str) -> list[str]:
    """The links of the elements of class `cls`, as written in the page (escaped)."""
    return re.findall(rf"<a class='{cls}' href='([^']*)'", html)


def test_an_item_carries_the_tags_its_badges_show() -> None:
    [fix, bins, other] = _board().panel("github.needs_reviewer").items
    assert (fix.tags, bins.tags, other.tags) == (["chores", "urgent"], ["garbage-day"], [])
    assert {"#chores", "#urgent"} <= set(fix.badges)


def test_the_filter_keeps_what_carries_any_of_the_tags() -> None:
    board = _board()
    both = tag_view.filtered(board, ["urgent", "garbage-day"])
    assert titles(both, "github.needs_reviewer") == ["Fix #chores #urgent", "Bins #garbage-day"]
    one = tag_view.filtered(board, ["garbage-day"])
    assert titles(one, "github.needs_reviewer") == ["Bins #garbage-day"]
    assert tag_view.filtered(board, []) is board


def test_an_item_with_a_part_carrying_a_tag_is_kept_whole() -> None:
    chores = tag_view.filtered(_board(), ["chores"])
    [errands] = chores.panel("notes.todos").items
    assert [part.title for part in errands.parts] == ["one #chores", "two"]


def test_panels_and_groups_left_empty_leave_the_page_but_focus_mode_still_knows_them() -> None:
    board = tag_view.filtered(_board(), ["garbage-day"])
    assert [(g.key, g.panel_keys) for g in board.groups] == [
        (ACT_NOW, ["act.github"]),
        ("github", ["github.fix_before_review", "github.needs_reviewer"]),
    ]
    assert board.focus_items("notes.todos") == []
    html = render.dashboard_page(_board(), set(), tags=["garbage-day"])
    assert "Todos from the work notes" not in html and "Other" not in html
    # An order dragged among only what's shown couldn't be saved whole.
    assert not re.search(r"<details[^>]* data-drag", html)


def test_act_now_and_focus_mode_are_filtered() -> None:
    board = tag_view.filtered(_board(), ["chores"])
    assert [i.title for i in board.act_now()] == [
        "Finish 2 quick todos: Errands",
        "Request a reviewer: Fix #chores #urgent",
    ]
    focused = board.focus_items("github.needs_reviewer") or []
    assert [item.title for _, item in focused] == ["Fix #chores #urgent"]
    page = render.focus_page(_board(), "github.needs_reviewer", 0, tags=["garbage-day"]) or ""
    assert "Bins #garbage-day" in page and "1 of 1" in page
    assert render.focus_page(_board(), ACT_NOW, 0, tags=["nothing"]) is not None


def test_when_nothing_carries_a_tag_the_page_says_so_and_offers_to_clear_it() -> None:
    html = render.dashboard_page(_board(), set(), tags=["gone"])
    assert "Nothing carries #gone. <a href='/'>Clear the filter</a>" in html
    # It stays selected, to clear, though nothing carries it.
    assert _hrefs(html, "badge tag selected") == ["/"]
    focus = render.focus_categories_page(_board(), ["gone"])
    assert "Nothing carries #gone. <a href='/focus'>Clear the filter</a>" in focus
    step = render.focus_page(_board(), ACT_NOW, 0, tags=["gone"]) or ""
    assert "<a href='/focus?cat=act'>Clear the filter</a>" in step


def test_the_drop_down_offers_every_tag_on_the_page_with_how_many_carry_it() -> None:
    board = _board()
    # PR 1 is in several panels and Act now, and counts once; its todo is another.
    assert tag_view.options(board) == [("chores", 2), ("garbage-day", 1), ("urgent", 1)]
    html = render.dashboard_page(board, set(), tags=["garbage-day"])
    bar = html.split("<nav class='tag-view'>")[1].split("</nav>")[0]
    assert re.findall(r"<option value='([^']*)'>", bar) == ["", "chores", "urgent"]
    assert "<option value='chores'>#chores (2)</option>" in html
    # The form adds to what's selected.
    assert "<input type='hidden' name='tag' value='garbage-day'>" in html
    assert render.dashboard_page(dashboard(), set()).count("tag-view") == 0  # no tags, no bar


def test_a_tags_badge_links_to_the_page_with_it_added() -> None:
    html = render.dashboard_page(_board(), set(), tags=["urgent"])
    assert "/?tag=urgent&amp;tag=chores" in _hrefs(html, "badge tag")
    assert "/?tag=urgent" in _hrefs(html, "badge tag")  # selected already: kept once
    assert re.search(r"<span class='badge'>#\d</span>", html)  # Act now's place is no tag
    unfiltered = render.dashboard_page(_board(), set())
    assert "/?tag=garbage-day" in _hrefs(unfiltered, "badge tag")
    page = render.focus_page(_board(), "github.needs_reviewer", 0, tags=["urgent"]) or ""
    assert "/focus?cat=github.needs_reviewer&amp;tag=urgent&amp;tag=chores" in _hrefs(
        page, "badge tag"
    )


def test_each_tag_clears_on_its_own_or_all_at_once() -> None:
    html = render.dashboard_page(_board(), set(), tags=["urgent", "garbage-day"])
    assert _hrefs(html, "badge tag selected") == ["/?tag=garbage-day", "/?tag=urgent"]
    assert "<a class='clear' href='/'>Clear all</a>" in html


def test_what_comes_back_to_the_page_keeps_the_selection() -> None:
    html = render.dashboard_page(_board(), set(), tags=["urgent"])
    assert "action='/refresh?tag=urgent'" in html  # the header's, and each group's
    assert "action='/layout/reset?tag=urgent'" in html
    assert "href='/focus?tag=urgent'" in html and "<a href='/?tag=urgent' class='logo'>" in html
    assert "action='/actions/act_now/tag?at=github.needs_reviewer&pos=0&tag=urgent'" in html
    focus = render.focus_categories_page(_board(), ["urgent"])
    assert "href='/focus?cat=act&amp;i=0&amp;tag=urgent'" in focus
    step = render.focus_page(_board(), ACT_NOW, 0, tags=["urgent"]) or ""
    # A focus page's buttons come back to it, whose address has the selection.
    assert "back=%2Ffocus%3Fcat%3Dact%26i%3D0%26tag%3Durgent" in step
    assert not re.search(r"action='/actions/[^']*[?&]tag=", step)  # nor the selection twice


def test_a_query_selects_tags_by_handle_and_ignores_what_names_none() -> None:
    query = {"tag": ["#Garbage Day", "garbage-day", "", "!!", "<script>"], "x": ["y"]}
    assert tag_view.selected(query) == ["garbage-day", "script"]
    html = render.dashboard_page(_board(), set(), tags=["a'b<c>"])  # escaped, however it came
    assert "a'b" not in html and "<c>" not in html
