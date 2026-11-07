import re
from datetime import timedelta
from urllib.parse import quote

from conftest import ME, NOW, dashboard, entry, pr, snapshot, stored, todo

from move_forward import page, panels, render
from move_forward.graph import Ref
from move_forward.page import Identity
from move_forward.plugins.notes.repository import NotesRepository
from move_forward.refresh import kept_item


def _dashboard() -> panels.Dashboard:
    return dashboard(
        slack=True,
        github=snapshot(mine=[pr(1, title="Fix <script> & stuff", requested_users=["alice"])]),
    )


def test_dashboard_page_escapes_and_links() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert "Fix &lt;script&gt; &amp; stuff" in html
    assert "<script" not in html.split("<main>")[1].split("</main>")[0]
    assert "href='https://github.com/acme/widgets/pull/1'" in html
    assert "built 12:00" in html
    assert "<link rel='icon' href='/favicon.svg' type='image/svg+xml'>" in html
    assert html.count("<a href='/' class='logo'") == 1


def test_only_act_now_is_open_by_default() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert "<details class='group' data-key='act' open><summary><h2>Act now</h2>" in html
    assert (
        "<details class='group' data-key='slack' data-drag><summary><h2>Slack</h2>"
        "<span class='count'>1</span>" in html
    )
    keys = re.findall(r"<details class='group' data-key='(\w+)'", html)
    assert keys == ["act", "notes", "github", "git", "slack"]  # Act now, then by priority
    assert len(re.findall(r"class='group' data-key='\w+' open>", html)) == 1


def test_each_plugin_says_when_it_last_refreshed_and_refreshes_on_a_click() -> None:
    built = _dashboard()
    html = render.dashboard_page(built, refreshing=frozenset())
    github = html.split("data-key='github'")[1].split("</summary>")[0]
    assert "<span class='refreshed' title='0m ago'>refreshed 12:00</span>" in github
    assert "<input type='hidden' name='plugin' value='github'>" in github
    assert "<strong>github:</strong>" not in html  # the footer leaves it to the summary
    yesterday = page.SourceStatus(fetched_at=NOW - timedelta(days=1, hours=2))
    failed = page.SourceStatus(fetched_at=NOW, error="gh: <not logged in>")
    built = built.model_copy(update={"sources": {"github": yesterday, "slack": failed}})
    html = render.dashboard_page(built, refreshing=frozenset())
    assert ">refreshed Oct 29 10:00</span>" in html
    assert "title='gh: &lt;not logged in&gt;'>refresh failed</span>" in html
    act_now = html.split("data-key='act'")[1].split("</summary>")[0]
    assert "refresh" not in act_now  # Act now isn't a plugin


def test_a_part_of_a_plugins_refresh_shows_in_its_header_not_the_footer() -> None:
    built = _dashboard()
    ok = built.sources["github"]
    parts = {"github.derived": page.SourceStatus(fetched_at=NOW)}
    quiet = built.model_copy(update={"sources": {"github": ok, **parts}})
    html = render.dashboard_page(quiet, refreshing=frozenset())
    assert "github.derived" not in html
    broken = {"github.derived": page.SourceStatus(fetched_at=NOW, error="boom")}
    built = built.model_copy(update={"sources": {"github": ok, **broken}})
    html = render.dashboard_page(built, refreshing=frozenset())
    github = html.split("data-key='github'")[1].split("</summary>")[0]
    assert "title='github.derived: boom'>refresh failed</span>" in github
    assert "<footer>" in html and "github.derived" not in html.split("<footer>")[1]


def test_refreshing_disables_the_buttons_and_reloads_sooner() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset({"github"}))
    assert ">refreshing…</span>" in html.split("data-key='github'")[1].split("</summary>")[0]
    assert "disabled" in html
    assert f"<body data-reload='{render.REFRESHING_RELOAD_SECONDS}'>" in html
    assert (
        f'<noscript><meta http-equiv="refresh" content="{render.REFRESHING_RELOAD_SECONDS}">'
        in html
    )
    assert f"<body data-reload='{render.PAGE_RELOAD_SECONDS}'>" in render.dashboard_page(
        _dashboard(), refreshing=frozenset()
    )


def test_refreshing_cycles_complete_favicon_frames() -> None:
    busy = render.dashboard_page(_dashboard(), refreshing=frozenset({"github"}))
    idle = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert "<span data-refreshing>refreshing…</span>" in busy
    assert "<span data-refreshing" not in idle
    assert "<link rel='icon' href='/favicon-refresh.svg' type='image/svg+xml'>" in busy
    assert "<link rel='icon' href='/favicon.svg' type='image/svg+xml'>" in idle
    assert render.REFRESH_ICON_SCRIPT in busy and render.REFRESH_ICON_SCRIPT in idle
    assert "mfd:replaced" in render.REFRESH_ICON_SCRIPT
    assert "setInterval" in render.REFRESH_ICON_SCRIPT
    assert "clearInterval" in render.REFRESH_ICON_SCRIPT
    assert "'/favicon-refresh-2.svg'" in render.REFRESH_ICON_SCRIPT


def test_the_page_reloads_in_the_background_instead_of_navigating() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert render.RELOAD_SCRIPT in html
    assert "location.reload" not in html
    assert "mfd:replaced" in render.OPEN_SECTIONS_SCRIPT + render.LAYOUT_SCRIPT


def test_dashboard_actions_replace_content_and_keep_scroll() -> None:
    script = render.RELOAD_SCRIPT
    assert "document.addEventListener('submit'" in script
    assert "new URL(form.action).pathname.startsWith('/actions/')" in script
    assert "body: new URLSearchParams(new FormData(form))" in script
    assert "scrollTo(x, y)" in script
    assert "started === revision" in script


def test_notifications_use_a_server_stream_with_a_polling_fallback() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert "new EventSource('/notifications/stream')" in html
    assert "source.onmessage" in html
    assert "else { poll(); setInterval(poll, NOTIFY_SECONDS * 1000); }" in html


def test_sections_carry_keys_for_the_open_state_script() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert "<details class='group' data-key='act' open>" in html
    assert (
        "<details class='panel' id='slack.nudges' data-key='slack.nudges' data-drag open>" in html
    )
    assert (
        "<details class='panel empty' id='github.needs_reviewer' data-key='github.needs_reviewer' data-drag>"
        in html
    )
    assert "details[data-key]" in html and "localStorage.setItem('mfd.open'" in html
    assert "localStorage.getItem('mfd.open')" in html and "location.search" not in html
    # No [git_stack], so the Stacks panel is absent and git's group still renders.
    assert "data-key='stacks'" not in html and "data-key='git'" in html


def test_panel_renders_nested_items_with_persistent_collapse_controls() -> None:
    root = page.Item(
        title="PR 1", subjects=[Ref(kind="pull_request", id="one")], children_open=False
    )
    child = page.Item(
        title="PR 2", parent_anchor=root.anchor, subjects=[Ref(kind="pull_request", id="two")]
    )
    grandchild = page.Item(
        title="PR 3", parent_anchor=child.anchor, subjects=[Ref(kind="pull_request", id="three")]
    )
    group = page.Panel(key="prs", title="PRs", description="", items=[grandchild, root, child])
    built = panels.Dashboard(
        generated_at=NOW,
        identities=[],
        panels=[group],
        groups=[page.Group(key="test", title="Test", panel_keys=["prs"], open=True)],
        sources={},
    )
    html = render.dashboard_page(built, refreshing=frozenset(), spot=("prs", 0))
    assert "<span class='count'>3</span>" in html
    assert f"data-key='children:{root.anchor}'><summary>Related items (1)</summary>" in html
    assert f"data-key='children:{child.anchor}' open><summary>Related items (1)</summary>" in html
    assert html.index("PR 1") < html.index("PR 2") < html.index("PR 3")
    assert html.index("id='spot'") < html.index("PR 3")


def test_actions_render_as_forms_with_hidden_fields() -> None:
    item = page.Item(
        title="x",
        actions=[
            page.Action(
                name="delete_branch",
                label="Delete",
                fields={"repo": "/r", "branch": "a'b"},
                confirm="Sure?",
            )
        ],
    )
    dashboard = panels.Dashboard(
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME)],
        panels=[
            page.Panel(
                key="cleanup",
                title="Cleanup",
                description="",
                items=[item],
                actions=[page.Action(name="delete_branches", label="All", fields={"targets": "t"})],
            )
        ],
        groups=[page.Group(key="g", title="G", panel_keys=["cleanup"], open=True)],
        sources={},
    )
    html = render.dashboard_page(dashboard, refreshing=frozenset(), notice="deleted 1 <branch>")
    assert (
        "<form method='post' action='/actions/delete_branch?at=cleanup&pos=0' class='action'"
        in html
    )
    assert "onsubmit='return confirm(&#x27;Sure?&#x27;)'" in html
    assert "<input type='hidden' name='branch' value='a&#x27;b'>" in html
    assert (
        "<form method='post' action='/actions/delete_branches?at=cleanup' class='action'>" in html
    )
    assert "<span class='notice'>deleted 1 &lt;branch&gt;</span>" in html


def test_focus_categories_page_lists_counts_and_links_non_empty_ones() -> None:
    html = render.focus_categories_page(_dashboard())
    assert (
        "<li class='category'><a href='/focus?cat=all&amp;i=0'>All</a><span class='count'>2" in html
    )
    assert "<a href='/focus?cat=slack&amp;i=0'>Slack</a>" in html
    assert "<li class='category sub'><a href='/focus?cat=slack.nudges&amp;i=0'>" in html
    assert "<a href='/focus?cat=act&amp;i=0'>Act now</a><span class='count'>1</span>" in html
    assert "<li class='category sub empty'><span>Feedback to address</span>" in html


def test_focus_page_steps_through_items() -> None:
    built = dashboard(
        slack=True,
        github=snapshot(mine=[pr(1, requested_users=["alice"]), pr(2, merge_state="DIRTY")]),
    )
    first = render.focus_page(built, panels.FOCUS_ALL, 0)
    assert first is not None
    assert "<h2>Fix before review</h2>" in first and pr(2).title in first
    assert pr(1).title not in first
    assert "<span class='focus-position'>1 of 5</span>" in first
    assert "<span class='button secondary disabled prev'>" in first
    assert "<a class='button secondary next' href='/focus?cat=all&amp;i=1'>" in first
    assert "ArrowLeft" in first and "data-reload" not in first

    last = render.focus_page(built, panels.FOCUS_ALL, 99)  # clamps to the last item
    assert last is not None
    assert "<h2>Nudge reviewers</h2>" in last and "5 of 5" in last
    assert "<a class='button secondary prev' href='/focus?cat=all&amp;i=3'>" in last
    assert "disabled next" in last
    assert "/slack/ping?pr=" + quote(pr(1).url, safe="") in last

    empty = render.focus_page(built, "github.feedback", 0)
    assert empty is not None and "Nothing here" in empty
    assert render.focus_page(built, "nope", 0) is None


def test_focus_page_actions_come_back_to_it() -> None:
    item = page.Item(
        title="x", actions=[page.Action(name="delete_branch", label="Delete", fields={"b": "1"})]
    )
    dashboard = panels.Dashboard(
        groups=[page.Group(key="git", title="Local", panel_keys=["cleanup"])],
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME)],
        panels=[page.Panel(key="cleanup", title="Cleanup", description="", items=[item])],
        sources={},
    )
    html = render.focus_page(dashboard, "cleanup", 0, notice="done")
    assert html is not None
    assert "action='/actions/delete_branch?back=%2Ffocus%3Fcat%3Dcleanup%26i%3D0'" in html
    assert "<span class='notice'>done</span>" in html
    assert "/actions/delete_branch?at=cleanup&pos=0' class='action'" in render.dashboard_page(
        dashboard, refreshing=frozenset()
    )


def test_checkbox_actions_and_the_done_todo_view() -> None:
    built = dashboard(work_items=[entry("APP-2", [todo("a", "quick"), todo("b", "big")])])
    html = render.focus_page(built, "notes.todos", 0)
    assert html is not None
    assert (
        "<label class='check'><input type='checkbox' onchange='this.form.requestSubmit()'> Done"
        in html
    )
    assert "action='/actions/notes/todo_done?back=%2Ffocus%3Fcat%3Dnotes.todos%26i%3D0'" in html
    assert "<input type='hidden' name='todo' value='" in html
    assert "1 of 2" in html

    # `a` was ticked off: it stays on the page, ticked, and Next goes to what is now item 0.
    config, db = stored(work_items=[entry("APP-2", [todo("a", "quick"), todo("b", "big")])])
    todo_a = next(t.id for e in NotesRepository(db).entries() for t in e.todos if t.text == "a")
    NotesRepository(db).set_done(todo_a, NOW.date())
    done = kept_item(config, db, f"notes:{todo_a}")
    assert done is not None
    html = render.focus_page(built, "notes.todos", 0, done=done)
    assert html is not None
    assert (
        "<input type='checkbox' checked onchange" in html and "/actions/notes/todo_undone?" in html
    )
    assert "<span class='badge'>done</span>" in html
    assert "<span class='focus-position'>done · 2 to go</span>" in html
    assert "<a class='button secondary next' href='/focus?cat=notes.todos&amp;i=0'>" in html
    assert "Delete todo" not in html

    # Ticking the last item off leaves nothing else, but still shows the done one.
    empty = dashboard()
    html = render.focus_page(empty, "notes.todos", 0, done=done)
    assert html is not None and "Nothing here" not in html and "done · 0 to go" in html
    assert "disabled next" in html


def test_strong_links_get_a_class() -> None:
    item = page.Item(
        title="x",
        links=[
            page.Link(text="PR 1", url="https://g/1", strong=True, done=True),
            page.Link(text="PR 2", url="https://g/2"),
            page.Link(text="PR 3", url="https://g/3", done=True),
        ],
        actions=[
            page.Action(
                name="notes/todo_done", label="Done", fields={}, checkbox=True, suggested=True
            )
        ],
    )
    dashboard = panels.Dashboard(
        groups=[],
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME)],
        panels=[page.Panel(key="todos", title="T", description="", items=[item])],
        sources={},
    )
    html = render.focus_page(dashboard, "todos", 0)
    assert html is not None
    assert (
        "<a href='https://g/1' class='strong done' target='_blank' rel='noopener'>PR 1</a>" in html
    )
    assert "<a href='https://g/2' target='_blank' rel='noopener'>PR 2</a>" in html
    assert "<a href='https://g/3' class='done' target='_blank' rel='noopener'>PR 3</a>" in html
    assert "<label class='check suggested'>" in html


def test_a_text_fields_suggestions_are_offered_in_order_and_escaped() -> None:
    action = page.Action(
        name="github/assign",
        label="Assign",
        fields={"pr": "1"},
        input="Login",
        suggestions=["zed", "al<ice"],
    )
    html = render.buttons([action])
    list_id = re.search(r"list='(suggestions-[0-9a-f]+)'", html)
    assert list_id is not None
    assert f"<datalist id='{list_id[1]}'>" in html
    assert "<option value='zed'></option><option value='al&lt;ice'></option>" in html


def test_the_same_button_in_two_places_has_a_suggestion_list_each() -> None:
    action = page.Action(
        name="github/assign", label="Assign", fields={}, input="Login", suggestions=["zed"]
    )
    ids = [
        re.findall(r"<datalist id='([^']+)'", render.buttons([action], at=panel, pos=0))[0]
        for panel in ("act.github", "github.fix_before_review")
    ]
    assert ids[0] != ids[1]


def test_a_text_field_without_suggestions_has_no_list() -> None:
    action = page.Action(name="notes/todo_link", label="Link", fields={}, input="Jira key")
    assert "datalist" not in render.buttons([action])


def test_actions_with_an_input_render_a_text_field() -> None:
    item = page.Item(
        title="x",
        actions=[
            page.Action(
                name="notes/todo_link", label="Link", fields={"text": "x"}, input="Jira key"
            )
        ],
    )
    dashboard = panels.Dashboard(
        groups=[],
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME)],
        panels=[page.Panel(key="todos", title="T", description="", items=[item])],
        sources={},
    )
    html = render.focus_page(dashboard, "todos", 0)
    assert html is not None
    assert (
        "<input type='hidden' name='text' value='x'><input type='text' name='input'"
        " placeholder='Jira key' required><button type='submit' class='secondary small'>Link</button>"
        in html
    )


def test_only_web_and_dashboard_urls_become_links() -> None:
    item = page.Item(
        title="sneaky",
        url="javascript:alert(1)",
        links=[
            page.Link(text="also", url="javascript:alert(2)"),
            page.Link(text="ok", url="https://x/1"),
        ],
        actions=[page.Action(name="", label="Go", fields={}, href="javascript:alert(3)")],
    )
    html = render._item(item)  # pyright: ignore[reportPrivateUsage]
    assert "javascript:" not in html
    assert "<a href='https://x/1'" in html and "sneaky" in html and "also" in html


def test_an_item_carries_its_subjects_and_act_now_fields_for_the_link_script() -> None:
    def html_of(item: page.Item) -> str:
        panel = page.Panel(key="p", title="P", description="", items=[item])
        dashboard = panels.Dashboard(
            generated_at=NOW,
            identities=[Identity(tool="GitHub", login=ME)],
            panels=[panel],
            groups=[page.Group(key="g", title="G", panel_keys=["p"], open=True)],
            sources={},
        )
        return render.dashboard_page(dashboard, refreshing=frozenset())

    subject = Ref(kind="pull_request", id="https://g/1")
    opened = html_of(
        page.Item(title="x", subjects=[subject], act_now_fields='{"kind": "k"}', opened_at=NOW)
    )
    said = "{&quot;kind&quot;: &quot;pull_request&quot;, &quot;id&quot;: &quot;https://g/1&quot;}"
    assert "<li class='item opened' id='item-pull_request-https-g-1'" in opened
    assert f" data-opened='{{&quot;kind&quot;: &quot;k&quot;}}' data-subjects='[{said}]'>" in opened
    assert (
        f"<button type='button' class='button link-action secondary refetch' data-subjects='[{said}]'"
        in opened
    )
    plain = html_of(page.Item(title="x"))
    assert "<li class='item'>" in plain and "refetch" not in plain.split("<script>")[0]


def test_a_panels_buttons_return_to_that_panel_instead_of_the_top() -> None:
    item = page.Item(title="x", actions=[page.Action(name="a", label="Go", fields={})])
    panel = page.Panel(key="jira.jira_next", title="P", description="", items=[item])
    dashboard = panels.Dashboard(
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME)],
        panels=[panel],
        groups=[page.Group(key="g", title="G", panel_keys=[panel.key], open=True)],
        sources={},
    )
    html = render.dashboard_page(dashboard, refreshing=frozenset())
    assert "id='jira.jira_next'" in html
    assert "action='/actions/a?at=jira.jira_next&pos=0'" in html


def test_the_page_after_an_action_has_an_anchor_where_its_item_was() -> None:
    items = [page.Item(title=t) for t in ("a", "b")]
    panel = page.Panel(key="p", title="P", description="", items=items)
    dashboard = panels.Dashboard(
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME)],
        panels=[panel],
        groups=[page.Group(key="g", title="G", panel_keys=["p"], open=True)],
        sources={},
    )
    anchor = f"<li id='{render.SPOT_ID}' aria-hidden='true'></li>"

    def at(spot: render.Spot | None) -> str:
        return render.dashboard_page(dashboard, refreshing=frozenset(), spot=spot)

    assert anchor not in at(None)
    assert at(("p", 1)).index(anchor) < at(("p", 1)).index(">b<")  # between a and b
    assert at(("p", 1)).index(anchor) > at(("p", 1)).index(">a<")
    assert at(("p", 9)).index(anchor) > at(("p", 9)).index(">b<")  # its item was the last
    assert anchor not in at(("other", 0))


def test_items_carry_their_anchor_for_notifications_to_open() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=frozenset())
    assert re.search(r"<li class='item' id='item-pull_request-[^']+'", html)


def test_the_footer_says_who_i_am_in_each_tool() -> None:
    dashboard = panels.Dashboard(
        generated_at=NOW,
        identities=[Identity(tool="GitHub", login=ME), Identity(tool="Jira", login="me@x")],
        panels=[],
        groups=[],
        sources={},
    )
    html = render.dashboard_page(dashboard, refreshing=frozenset())
    assert f"as GitHub: {ME} · Jira: me@x" in html


def test_an_items_details_show_alongside_its_parts() -> None:
    item = page.Item(
        title="x", details=["why: quick"], parts=[page.Item(title="one"), page.Item(title="two")]
    )
    html = render.item_html(item)
    assert "why: quick" in html
    assert "<ul class='item-parts'>" in html


def test_a_listed_part_does_not_repeat_its_items_button() -> None:
    add = page.Action(name="notes/todo_add", label="Add todo", fields={"entry": "e"}, input="New")
    done = page.Action(name="notes/todo_done", label="Done", fields={"todo": "1"}, checkbox=True)
    opened = add.model_copy(update={"fields": {**add.fields, "opened": "{}"}})
    item = page.Item(
        title="e",
        actions=[opened],
        parts=[page.Item(title="one", actions=[done, add]), page.Item(title="two", actions=[add])],
    )
    html = render.item_html(item)
    assert html.count("/actions/notes/todo_add") == 1
    assert html.count("/actions/notes/todo_done") == 1


def test_a_buttons_choices_are_a_drop_down_beside_its_field() -> None:
    action = page.Action(
        name="notes/todo_add",
        label="Add",
        fields={},
        input="New",
        choices={"Unsized": "", "Q<uick": "quick"},
    )
    html = render.buttons([action])
    assert (
        "<select name='choice' aria-label='Add'><option value=''>Unsized</option>"
        "<option value='quick'>Q&lt;uick</option></select>"
    ) in html
    plain = action.model_copy(update={"choices": {}})
    assert "<select" not in render.buttons([plain])
