import re
from urllib.parse import quote

from conftest import ME, NOW, dashboard, pr, snapshot

from move_forward import panels, render


def _dashboard() -> panels.Dashboard:
    return dashboard(
        slack=True,
        github=snapshot(mine=[pr(1, title="Fix <script> & stuff", requested_users=["alice"])]),
    )


def test_dashboard_page_escapes_and_links() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=False)
    assert "Fix &lt;script&gt; &amp; stuff" in html
    assert "<script" not in html.split("<main>")[1].split("</main>")[0]
    assert "href='https://github.com/acme/widgets/pull/1'" in html
    assert "<strong>github:</strong> 0m ago" in html
    assert "built 12:00" in html
    assert "<link rel='icon' href='/favicon.svg' type='image/svg+xml'>" in html
    assert html.count("<a href='/' class='logo'") == 1


def test_open_query_parameter_overrides_the_defaults() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=False, open_keys={"my_prs", "feedback"})
    assert "<details class='group' data-key='my_prs' open>" in html
    assert "<details class='group' data-key='act'>" in html
    assert "<details class='panel empty' data-key='feedback' open>" in html
    assert "<details class='panel' data-key='nudges'>" in html
    everything_closed = render.dashboard_page(_dashboard(), refreshing=False, open_keys=set())
    assert " open>" not in everything_closed


def test_only_act_now_is_open_by_default() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=False)
    assert "<details class='group' data-key='act' open><summary><h2>Act now</h2>" in html
    assert (
        "<details class='group' data-key='my_prs'><summary><h2>My PRs</h2>"
        "<span class='count'>1</span>" in html
    )
    assert html.count("<details class='group' data-key='") == len(panels.GROUPS)
    assert len(re.findall(r"class='group' data-key='\w+' open>", html)) == 1
    assert html.count("<details class='group'") == len(panels.GROUPS)


def test_refreshing_disables_the_button_and_reloads_sooner() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=True)
    assert "disabled" in html
    assert f"<body data-reload='{render.REFRESHING_RELOAD_SECONDS}'>" in html
    assert (
        f'<noscript><meta http-equiv="refresh" content="{render.REFRESHING_RELOAD_SECONDS}">'
        in html
    )
    assert f"<body data-reload='{render.PAGE_RELOAD_SECONDS}'>" in render.dashboard_page(
        _dashboard(), refreshing=False
    )


def test_sections_carry_keys_for_the_open_state_script() -> None:
    html = render.dashboard_page(_dashboard(), refreshing=False)
    assert "<details class='group' data-key='act' open>" in html
    assert "<details class='panel' data-key='nudges' open>" in html
    assert "<details class='panel empty' data-key='needs_reviewer'>" in html
    assert "details[data-key]" in html and "'?open=' + open" in html
    assert "if (open === saved) return;" in html
    # No git-stack data, so the Stacks panel is absent and its group still renders.
    assert "data-key='stacks'" not in html and "data-key='local'" in html


def test_actions_render_as_forms_with_hidden_fields() -> None:
    item = panels.Item(
        title="x",
        actions=[
            panels.Action(
                name="delete_branch",
                label="Delete",
                fields={"repo": "/r", "branch": "a'b"},
                confirm="Sure?",
            )
        ],
    )
    dashboard = panels.Dashboard(
        generated_at=NOW,
        login=ME,
        panels=[
            panels.Panel(
                key="cleanup",
                title="Cleanup",
                description="",
                items=[item],
                actions=[
                    panels.Action(name="delete_branches", label="All", fields={"targets": "t"})
                ],
            )
        ],
        groups=[panels.Group(key="g", title="G", panel_keys=["cleanup"], open=True)],
        sources={},
    )
    html = render.dashboard_page(dashboard, refreshing=False, notice="deleted 1 <branch>")
    assert "<form method='post' action='/actions/delete_branch' class='action'" in html
    assert "onsubmit='return confirm(&#x27;Sure?&#x27;)'" in html
    assert "<input type='hidden' name='branch' value='a&#x27;b'>" in html
    assert "<form method='post' action='/actions/delete_branches' class='action'>" in html
    assert "<span class='notice'>deleted 1 &lt;branch&gt;</span>" in html


def test_focus_categories_page_lists_counts_and_links_non_empty_ones() -> None:
    html = render.focus_categories_page(_dashboard())
    assert (
        "<li class='category'><a href='/focus?cat=all&amp;i=0'>All</a><span class='count'>1" in html
    )
    assert "<a href='/focus?cat=my_prs&amp;i=0'>My PRs</a>" in html
    assert "<li class='category sub'><a href='/focus?cat=nudges&amp;i=0'>" in html
    assert "<li class='category empty'><span>Act now</span><span class='count'>0</span>" in html
    assert "<li class='category sub empty'><span>Feedback to address</span>" in html


def test_focus_page_steps_through_items() -> None:
    built = dashboard(
        slack=True,
        github=snapshot(mine=[pr(1, requested_users=["alice"]), pr(2, merge_state="DIRTY")]),
    )
    first = render.focus_page(built, panels.FOCUS_ALL, 0)
    assert first is not None
    assert "<h2>Merge conflicts</h2>" in first and pr(2).title in first
    assert pr(1).title not in first
    assert "<span class='focus-position'>1 of 3</span>" in first
    assert "<span class='button secondary disabled prev'>" in first
    assert "<a class='button secondary next' href='/focus?cat=all&amp;i=1'>" in first
    assert "ArrowLeft" in first and "data-reload" not in first

    last = render.focus_page(built, panels.FOCUS_ALL, 99)  # clamps to the last item
    assert last is not None
    assert "<h2>Nudge reviewers</h2>" in last and "3 of 3" in last
    assert "<a class='button secondary prev' href='/focus?cat=all&amp;i=1'>" in last
    assert "disabled next" in last
    assert "/ping?pr=" + quote(pr(1).url, safe="") in last

    empty = render.focus_page(built, "feedback", 0)
    assert empty is not None and "Nothing here" in empty
    assert render.focus_page(built, "nope", 0) is None


def test_focus_page_actions_come_back_to_it() -> None:
    item = panels.Item(
        title="x", actions=[panels.Action(name="delete_branch", label="Delete", fields={"b": "1"})]
    )
    dashboard = panels.Dashboard(
        generated_at=NOW,
        login=ME,
        panels=[panels.Panel(key="cleanup", title="Cleanup", description="", items=[item])],
        sources={},
    )
    html = render.focus_page(dashboard, "cleanup", 0, notice="done")
    assert html is not None
    assert "action='/actions/delete_branch?back=%2Ffocus%3Fcat%3Dcleanup%26i%3D0'" in html
    assert "<span class='notice'>done</span>" in html
    assert "/actions/delete_branch' class='action'" in render.dashboard_page(
        dashboard, refreshing=False
    )


def test_checkbox_actions_and_the_done_todo_view() -> None:
    from move_forward.domain import Todo, WorkItem

    entry = WorkItem(
        title="APP-2",
        fields={},
        todos=[Todo(text="a", size="quick"), Todo(text="b", size="big")],
        text="APP-2",
    )
    built = dashboard(work_items=[entry])
    html = render.focus_page(built, "todos", 0)
    assert html is not None
    assert (
        "<label class='check'><input type='checkbox' onchange='this.form.requestSubmit()'> Done"
        in html
    )
    assert "action='/actions/todo_done?back=%2Ffocus%3Fcat%3Dtodos%26i%3D0'" in html
    assert "<input type='hidden' name='text' value='a'>" in html
    assert "1 of 2" in html

    # `a` was ticked off: it stays on the page, ticked, and Next goes to what is now item 0.
    done = built.done_todo("APP-2", "a")
    html = render.focus_page(built, "todos", 0, done=done)
    assert html is not None
    assert "<input type='checkbox' checked onchange" in html and "/actions/todo_undone?" in html
    assert "<span class='badge'>done</span>" in html
    assert "<span class='focus-position'>done · 2 to go</span>" in html
    assert "<a class='button secondary next' href='/focus?cat=todos&amp;i=0'>" in html
    assert "Delete todo" not in html

    # Ticking the last item off leaves nothing else, but still shows the done one.
    empty = dashboard()
    html = render.focus_page(empty, "todos", 0, done=done)
    assert html is not None and "Nothing here" not in html and "done · 0 to go" in html
    assert "disabled next" in html


def test_strong_links_get_a_class() -> None:
    item = panels.Item(
        title="x",
        links=[
            panels.Link(text="PR 1", url="https://g/1", strong=True, pr_state="merged"),
            panels.Link(text="PR 2", url="https://g/2"),
            panels.Link(text="PR 3", url="https://g/3", pr_state="closed"),
        ],
        actions=[
            panels.Action(name="todo_done", label="Done", fields={}, checkbox=True, suggested=True)
        ],
    )
    dashboard = panels.Dashboard(
        generated_at=NOW,
        login=ME,
        panels=[panels.Panel(key="todos", title="T", description="", items=[item])],
        sources={},
    )
    html = render.focus_page(dashboard, "todos", 0)
    assert html is not None
    assert (
        "<a href='https://g/1' class='strong merged' target='_blank' rel='noopener'>PR 1</a>"
        in html
    )
    assert "<a href='https://g/2' target='_blank' rel='noopener'>PR 2</a>" in html
    assert "<a href='https://g/3' class='closed' target='_blank' rel='noopener'>PR 3</a>" in html
    assert "<label class='check suggested'>" in html


def test_actions_with_an_input_render_a_text_field() -> None:
    item = panels.Item(
        title="x",
        actions=[
            panels.Action(name="todo_link", label="Link", fields={"text": "x"}, input="Jira key")
        ],
    )
    dashboard = panels.Dashboard(
        generated_at=NOW,
        login=ME,
        panels=[panels.Panel(key="todos", title="T", description="", items=[item])],
        sources={},
    )
    html = render.focus_page(dashboard, "todos", 0)
    assert html is not None
    assert (
        "<input type='hidden' name='text' value='x'><input type='text' name='input'"
        " placeholder='Jira key' required><button type='submit' class='secondary small'>Link</button>"
        in html
    )
