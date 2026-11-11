"""The tag view: the page filtered to what carries one of the tags in its address."""

import re

from conftest import NOW, dashboard, entry, issue, pr, snapshot, stored, thread, titles, todo

from move_forward import refresh, render, tag_view
from move_forward.page import SourceStatus
from move_forward.panels import ACT_NOW, Dashboard
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.refresh import build_dashboard

_ERRANDS = [entry("Errands", [todo("one #chores", "quick"), todo("two", "quick")])]


def _selecting(tags: list[str], **overrides: object) -> Dashboard:
    """The dashboard as the page builds it with `tags` selected, from what `stored` stores."""
    config, db = stored(**overrides)
    return build_dashboard(config, db, NOW, selected=tags)


def _board(*tags: str) -> Dashboard:
    """Three PRs, two tagged in their titles, and an entry with a tagged todo and one not, built
    with `tags` selected."""
    return _selecting(
        list(tags),
        github=snapshot(
            mine=[
                pr(1, title="Fix #chores #urgent"),
                pr(2, title="Bins #garbage-day"),
                pr(3, title="Other"),
            ]
        ),
        work_items=_ERRANDS,
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
    board = tag_view.filtered(_board("garbage-day"), ["garbage-day"])
    assert [(g.key, g.panel_keys) for g in board.groups] == [
        (ACT_NOW, ["act.github"]),
        ("github", ["github.fix_before_review", "github.needs_reviewer"]),
    ]
    assert board.focus_items("notes.todos") == []
    html = render.dashboard_page(_board("garbage-day"), set(), tags=["garbage-day"])
    assert "Todos from the work notes" not in html and "Other" not in html
    # The sections' open state keeps those left off (see `OPEN_SECTIONS_SCRIPT`).
    assert "<main data-filtered>" in html
    assert "<main>" in render.dashboard_page(_board(), set())
    # An order dragged among only what's shown couldn't be saved whole.
    assert not re.search(r"<details[^>]* data-drag", html)


def test_act_now_shows_the_top_of_what_carries_a_tag_not_what_of_its_top_does() -> None:
    late = pr(50, title="Late #chores")
    overrides = {"github": snapshot(mine=[*(pr(n) for n in range(1, 12)), late])}
    assert "Late #chores" not in str(dashboard(**overrides).act_now())  # below Act now's cut
    shown = _selecting(["chores"], **overrides)
    [item] = tag_view.filtered(shown, ["chores"]).act_now()
    assert "Late #chores" in item.title and item.badges[0] == "#1"
    html = render.dashboard_page(shown, set(), tags=["chores"])
    assert "<span class='badge'>#1</span>" in html and "Late #chores" in html


def test_focus_mode_steps_only_through_the_parts_carrying_a_tag() -> None:
    laundry = entry("Laundry #chores", [todo("three"), todo("four")])
    shown = _selecting(["chores"], work_items=[*_ERRANDS, laundry])
    page = render.focus_page(shown, "notes.todos", 0, tags=["chores"])
    assert page is not None and "1 of 3" in page and "one #chores" in page
    assert ">two<" not in page
    focused = tag_view.focused(shown, ["chores"]).focus_items("notes.todos") or []
    # An entry carrying the tag itself has all its todos as steps.
    assert [item.title for _, item in focused] == ["one #chores", "three", "four"]
    # The first page counts the steps the walk has.
    categories = render.focus_categories_page(shown, ["chores"])
    assert re.search(r">Todos from the work notes</a><span class='count'>3</span>", categories)
    # Act now's item is a step whole, as on the dashboard.
    [act, *_] = tag_view.focused(shown, ["chores"]).act_now()
    assert [part.title for part in act.parts] == ["one #chores", "two"]


def test_a_plugin_whose_group_is_left_off_keeps_its_status_in_the_footer() -> None:
    def board(*tags: str) -> Dashboard:
        built = _selecting(list(tags), github=snapshot(mine=[pr(1)]), work_items=_ERRANDS)
        return built.model_copy(
            update={
                "sources": {"github": SourceStatus(fetched_at=NOW, error="boom")},
                "plugin_problems": {"github": ["no notifications"]},
            }
        )

    unfiltered = render.dashboard_page(board(), set()).split("<footer>")[1]
    assert "boom" not in unfiltered and "no notifications" not in unfiltered  # its group says
    footer = render.dashboard_page(board("chores"), set(), tags=["chores"]).split("<footer>")[1]
    assert "<strong>GitHub:</strong>" in footer and "boom" in footer
    assert "no notifications" in footer and "<strong>github:</strong>" not in footer  # once
    assert "<form method='post' action='/refresh?tag=chores' class='refresh'>" in footer
    assert "<input type='hidden' name='plugin' value='github'>" in footer


def test_act_now_and_focus_mode_are_filtered() -> None:
    board = tag_view.filtered(_board("chores"), ["chores"])
    assert [i.title for i in board.act_now()] == [
        "Finish 2 quick todos: Errands",
        "Request a reviewer: Fix #chores #urgent",
    ]
    focused = board.focus_items("github.needs_reviewer") or []
    assert [item.title for _, item in focused] == ["Fix #chores #urgent"]
    page = (
        render.focus_page(_board("garbage-day"), "github.needs_reviewer", 0, tags=["garbage-day"])
        or ""
    )
    assert "Bins #garbage-day" in page and "1 of 1" in page
    assert render.focus_page(_board("nothing"), ACT_NOW, 0, tags=["nothing"]) is not None


def test_act_now_keeps_a_joined_items_members_whichever_carries_the_tag() -> None:
    # The PR's branch names APP-2, so the ticket's move joins the PR's item.
    config, db = stored(
        github=snapshot(mine=[pr(2, title="Fix #chores", threads=[thread("bob")])]),
        jira=JiraSnapshot(issues=[issue("APP-2", status="In Progress")]),
    )
    shown = build_dashboard(config, db, NOW, selected=["chores"])
    [item] = tag_view.filtered(shown, ["chores"]).act_now()
    assert item.title == "Reply: Fix #chores"
    assert "then: Move to In Review: APP-2 Do the thing" in item.details
    snooze = next(a for a in item.actions if a.label == "Snooze")
    assert refresh.run_action(config, db, snooze.name, snooze.fields) == "snoozed until tomorrow"
    assert build_dashboard(config, db, NOW).act_now() == []  # the ticket's move too
    # Only the ticket carrying it, the PR's step still comes first.
    labelled = _selecting(
        ["chores"],
        github=snapshot(mine=[pr(2, threads=[thread("bob")])]),
        jira=JiraSnapshot(issues=[issue("APP-2", status="In Progress", labels=["chores"])]),
    )
    [item] = tag_view.filtered(labelled, ["chores"]).act_now()
    assert item.title.startswith("Reply: ")
    assert "then: Move to In Review: APP-2 Do the thing" in item.details


def test_when_nothing_carries_a_tag_the_page_says_so_and_offers_to_clear_it() -> None:
    gone = _board("gone")
    html = render.dashboard_page(gone, set(), tags=["gone"])
    assert "Nothing carries #gone. <a href='/'>Clear the filter</a>" in html
    # It stays selected, to clear, though nothing carries it.
    assert _hrefs(html, "badge tag selected") == ["/"]
    focus = render.focus_categories_page(gone, ["gone"])
    assert "Nothing carries #gone. <a href='/focus'>Clear the filter</a>" in focus
    step = render.focus_page(gone, ACT_NOW, 0, tags=["gone"]) or ""
    assert "<a href='/focus?cat=act'>Clear the filter</a>" in step


def test_the_drop_down_offers_every_tag_on_the_page_with_how_many_carry_it() -> None:
    board = _board()
    # PR 1 is in several panels and Act now, and counts once; its todo is another.
    assert tag_view.options(board) == [("chores", 2), ("garbage-day", 1), ("urgent", 1)]
    # Filtered, it offers the tags of the plugins' panels before the filter.
    html = render.dashboard_page(_board("garbage-day"), set(), tags=["garbage-day"])
    bar = html.split("<nav class='tag-view'>")[1].split("</nav>")[0]
    assert re.findall(r"<option value='([^']*)'>", bar) == ["", "chores", "urgent"]
    assert "<option value='chores'>#chores (2)</option>" in html
    # The form adds to what's selected.
    assert "<input type='hidden' name='tag' value='garbage-day'>" in html
    assert render.dashboard_page(dashboard(), set()).count("tag-view") == 0  # no tags, no bar


def test_a_tags_badge_links_to_the_page_with_it_added() -> None:
    html = render.dashboard_page(_board("urgent"), set(), tags=["urgent"])
    assert "/?tag=urgent&amp;tag=chores" in _hrefs(html, "badge tag")
    assert "/?tag=urgent" in _hrefs(html, "badge tag")  # selected already: kept once
    assert re.search(r"<span class='badge'>#\d</span>", html)  # Act now's place is no tag
    unfiltered = render.dashboard_page(_board(), set())
    assert "/?tag=garbage-day" in _hrefs(unfiltered, "badge tag")
    page = render.focus_page(_board("urgent"), "github.needs_reviewer", 0, tags=["urgent"]) or ""
    assert "/focus?cat=github.needs_reviewer&amp;tag=urgent&amp;tag=chores" in _hrefs(
        page, "badge tag"
    )


def test_each_tag_clears_on_its_own_or_all_at_once() -> None:
    tags = ["urgent", "garbage-day"]
    html = render.dashboard_page(_board(*tags), set(), tags=tags)
    assert _hrefs(html, "badge tag selected") == ["/?tag=garbage-day", "/?tag=urgent"]
    assert "<a class='clear' href='/'>Clear all</a>" in html


def test_what_comes_back_to_the_page_keeps_the_selection() -> None:
    board = _board("urgent")
    html = render.dashboard_page(board, set(), tags=["urgent"])
    assert "action='/refresh?tag=urgent'" in html  # the header's, and each group's
    assert "action='/layout/reset?tag=urgent'" in html
    assert "href='/focus?tag=urgent'" in html and "<a href='/?tag=urgent' class='logo'>" in html
    assert "action='/actions/act_now/tag?at=github.needs_reviewer&pos=0&tag=urgent'" in html
    focus = render.focus_categories_page(board, ["urgent"])
    assert "href='/focus?cat=act&amp;i=0&amp;tag=urgent'" in focus
    step = render.focus_page(board, ACT_NOW, 0, tags=["urgent"]) or ""
    # A focus page's buttons come back to it, whose address has the selection.
    assert re.search(r"back=%2Ffocus%3Fcat%3Dact%26i%3D0%26kept%3D[\w-]+%26tag%3Durgent'", step)
    assert not re.search(r"action='/actions/[^']*[?&]tag=", step)  # nor the selection twice


def test_a_query_selects_tags_by_handle_and_ignores_what_names_none() -> None:
    query = {"tag": ["#Garbage Day", "garbage-day", "", "!!", "<script>"], "x": ["y"]}
    assert tag_view.selected(query) == ["garbage-day", "script"]
    odd = "a'b<c>"  # escaped, however it came
    html = render.dashboard_page(_board(odd), set(), tags=[odd])
    assert "a'b" not in html and "<c>" not in html
