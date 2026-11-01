"""Core's panels and the Dashboard: todos, on ice, nudges, groups and focus mode."""

from datetime import UTC, datetime

from conftest import (
    REPO,
    dashboard,
    entry,
    issue,
    local_snapshot,
    panel,
    pings,
    pr,
    snapshot,
    stacks,
    stored,
    titles,
    todo,
)

from move_forward import panels
from move_forward.plugins.git.models import LocalRepo, LocalSnapshot
from move_forward.plugins.git_stack.models import StackBranch
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.notes.panels import ADD_TODO_INPUT
from move_forward.refresh import kept_item

build = dashboard


def test_todos_put_close_outs_first_and_blocked_last() -> None:
    close_out = entry("APP-1 almost", [todo("reply", "quick")])
    mixed = entry("APP-2 mixed", [todo("big one", "big"), todo("small", "quick")])
    blocked = entry("APP-3 stuck", [todo("x", "quick")], **{"Blocked on": ["reviews"]})
    unblocked = entry("APP-4 fine", [todo("x", "quick")], **{"Blocked on": ["nothing"]})
    done = entry("APP-5 done", [])
    dashboard = build(work_items=[mixed, blocked, close_out, done, unblocked])
    items = panel(dashboard, "todos").items
    assert [i.title for i in items] == ["APP-1 almost", "APP-4 fine", "APP-2 mixed", "APP-3 stuck"]
    assert items[0].badges == ["close-out: 1 quick todo(s)"]
    assert items[2].details == ["[quick] small", "[big] big one"]
    assert items[3].badges == ["close-out: 1 quick todo(s)", "blocked: reviews"]


def test_every_panel_is_in_exactly_one_group() -> None:
    dashboard = build(
        slack=True,
        local=local_snapshot(),
        stacks=stacks(StackBranch(name="main")),
        jira=JiraSnapshot(issues=[]),
    )
    grouped = [key for group in dashboard.groups for key in group.panel_keys]
    assert sorted(grouped) == sorted(p.key for p in dashboard.panels)
    assert dashboard.panel("feedback").key == "feedback"


def test_build_with_nothing_stored() -> None:
    dashboard = build()
    assert all(p.items == [] for p in dashboard.panels)
    assert dashboard.generated_at == datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_focus_order_covers_every_panel_in_the_skills_order() -> None:
    dashboard = build(
        local=LocalSnapshot(
            repos=[
                LocalRepo(
                    dir=REPO,
                    github_repo="acme/widgets",
                    default_branch="main",
                    worktrees=[],
                    branches=[],
                )
            ]
        ),
        stacks=stacks(StackBranch(name="main"), dir=REPO),
    )
    assert {p.key for p in dashboard.panels} <= set(panels.FOCUS_ORDER)
    assert len(set(panels.FOCUS_ORDER)) == len(panels.FOCUS_ORDER)
    assert panels.FOCUS_ORDER.index("reviews_for_me") < panels.FOCUS_ORDER.index("feedback")
    assert panels.FOCUS_ORDER.index("cleanup") < panels.FOCUS_ORDER.index("iced")


def test_focus_items_by_category() -> None:
    dashboard = build(
        slack=True,
        github=snapshot(
            mine=[pr(1, requested_users=["alice"]), pr(2, merge_state="DIRTY")],
            direct_requests=[pr(3, author="bob")],
        ),
    )
    everything = dashboard.focus_items(panels.FOCUS_ALL)
    assert everything is not None
    keys = [panel.key for panel, _ in everything]
    assert keys.index("reviews_for_me") < keys.index("conflicts") < keys.index("needs_reviewer")
    assert [i.title for _, i in everything if _.key == "reviews_for_me"] == [pr(3).title]
    act = dashboard.focus_items("act")
    assert act is not None and [panel.key for panel, _ in act] == ["reviews_for_me", "conflicts"]
    one = dashboard.focus_items("nudges")
    assert one is not None and [i.title for _, i in one] == [pr(1).title]
    assert dashboard.focus_items("nope") is None
    assert dashboard.focus_title("act") == "Act now"
    assert dashboard.focus_title(panels.FOCUS_ALL) == "All"
    assert dashboard.focus_title("nope") is None


def test_focus_shows_todos_one_at_a_time() -> None:
    mixed = entry(
        "APP-2 mixed",
        [todo("big one", "big"), todo("small", "quick")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    blocked = entry("APP-3 stuck", [todo("x")], **{"Blocked on": ["reviews"]})
    dashboard = build(work_items=[mixed, blocked])
    assert titles(dashboard, "todos") == ["APP-2 mixed", "APP-3 stuck"]  # the page is unchanged
    focused = dashboard.focus_items("todos")
    assert focused is not None
    assert [(i.title, i.subtitle, i.badges) for _, i in focused] == [
        ("small", "APP-2 mixed", ["quick"]),
        ("big one", "APP-2 mixed", ["big"]),
        ("x", "APP-3 stuck", ["unsized", "blocked: reviews"]),
    ]
    assert focused[0][1].url is None  # the todo doesn't say which PR it's about
    assert [link.text for link in focused[0][1].links] == ["PR 7"]


def test_todo_parts_carry_their_actions_and_a_done_todo_can_be_kept() -> None:
    noted = entry(
        "APP-2 mixed",
        [todo("small", "quick")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    dashboard = build(work_items=[noted])
    focused = dashboard.focus_items("todos")
    assert focused is not None
    [(_, part)] = focused
    assert [(a.name, a.checkbox, a.checked) for a in part.actions] == [
        ("notes/todo_done", True, False),
        ("notes/todo_delete", False, False),
        ("notes/todo_link", False, False),
        ("notes/todo_add", False, False),
    ]
    todo_id = part.actions[0].fields["todo"]
    assert part.actions[0].fields == {"todo": todo_id}  # addressed by id
    assert part.actions[1].confirm is not None and "small" in part.actions[1].confirm
    # The entry on the page only gets the Add todo field, not the todo's own actions.
    assert [a.name for a in panel(dashboard, "todos").items[0].actions] == ["notes/todo_add"]

    config, db = stored(work_items=[noted])
    done = kept_item(config, db, f"notes:{todo_id}")
    assert done is not None
    assert (done.title, done.subtitle) == ("small", "APP-2 mixed")
    assert done.url == "https://github.com/acme/widgets/pull/7"
    assert [(a.name, a.checked) for a in done.actions] == [("notes/todo_done", False)] + [
        (a.name, False) for a in done.actions[1:]
    ]  # still open: only a ticked todo offers undo
    assert kept_item(config, db, "notes:999") is None
    assert kept_item(config, db, "notes:x") is None


def test_todo_parts_put_the_prs_they_mention_first_in_bold() -> None:
    urls = [f"https://github.com/acme/widgets/pull/{n}" for n in (11, 222, 3333)]
    noted = entry(
        "APP-2",
        [
            todo("get it merged"),
            todo("reply on #222 then rebase 3333"),
            todo(f"check {urls[0]} plans; 1222 is not a PR"),
            todo("follow up on 3333"),
        ],
        Where=urls,
    )
    github = snapshot(
        mine=[pr(222)], merged_recent=[pr(11, state="MERGED")], closed_recent=[pr(3333)]
    )
    focused = build(work_items=[noted], github=github).focus_items("todos")
    assert focused is not None
    parts = [part for _, part in focused]
    assert [link.pr_state for link in parts[0].links] == ["merged", "open", "closed"]
    # Only the todo whose every mentioned PR is merged or closed is suggested as done.
    assert [p.actions[0].suggested for p in parts] == [False, False, True, True]
    assert parts[2].badges == ["unsized", "its PRs are done"]
    assert parts[1].badges == ["unsized"]
    assert [(l.text, l.strong) for l in parts[0].links] == [
        ("PR 11", False),
        ("PR 222", False),
        ("PR 3333", False),
    ]
    assert parts[0].url is None
    assert [(l.text, l.strong) for l in parts[1].links] == [
        ("PR 222", True),
        ("PR 3333", True),
        ("PR 11", False),
    ]
    assert parts[1].url == urls[1]
    assert [(l.text, l.strong) for l in parts[2].links] == [
        ("PR 11", True),
        ("PR 222", False),
        ("PR 3333", False),
    ]


def test_todo_links_include_the_tickets_and_prs_only_the_todo_mentions() -> None:
    noted = entry(
        "APP-2 thing",
        [todo("ask about APP-9 and APP-9 again https://github.com/acme/other/pull/4")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    github = snapshot(merged_recent=[pr(7, state="MERGED")])
    dashboard = build(work_items=[noted], github=github, jira_site="https://j.example/")
    focused = dashboard.focus_items("todos")
    assert focused is not None
    [(_, part)] = focused
    # The PR named in the todo is one of the entry's PRs too; the todo's own come first.
    assert [(l.text, l.url, l.strong, l.pr_state) for l in part.links] == [
        ("PR 4", "https://github.com/acme/other/pull/4", True, None),
        ("PR 7", "https://github.com/acme/widgets/pull/7", False, "merged"),
        ("APP-9", "https://j.example/browse/APP-9", True, None),
    ]
    assert part.url == "https://github.com/acme/other/pull/4"
    # An unknown-state PR is not evidence the todo is done.
    assert part.actions[0].suggested is False
    assert [a.name for a in part.actions] == [
        "notes/todo_done",
        "notes/todo_delete",
        "notes/todo_link",
        "notes/todo_add",
    ]
    assert part.actions[2].input == "Jira key or PR URL"
    # Without a Jira site, keys aren't linked.
    [(_, part)] = build(work_items=[noted]).focus_items("todos") or []
    assert [l.text for l in part.links] == ["PR 4", "PR 7"]


def test_my_prs_tickets_and_entries_get_an_add_todo_field() -> None:
    covered = pr(1, head="me/APP-1-x")  # APP-1 has an entry
    by_url = pr(2, head="me/no-key")
    fresh = pr(3, head="me/APP-3-y", title="Do y")
    no_key = pr(4, head="me/plain", title="Plain", body="no ticket")
    entries = [
        entry("APP-1 Fix", [todo("t")]),
        entry("Some PR work", [todo("u")], Where=[by_url.url]),
    ]
    dashboard = build(
        github=snapshot(
            mine=[covered, by_url, fresh, no_key], direct_requests=[pr(9, author="bob")]
        ),
        jira=JiraSnapshot(
            issues=[issue("APP-3", summary="Why not"), issue("APP-8", summary="Next")]
        ),
        work_items=entries,
    )

    def add(panel_key: str, title: str) -> dict[str, str]:
        item = next(i for i in panel(dashboard, panel_key).items if i.title == title)
        [action] = [a for a in item.actions if a.name == "notes/todo_add"]
        assert action.input == ADD_TODO_INPUT
        return action.fields

    assert add("needs_reviewer", covered.title) == {"entry": "APP-1 Fix"}
    assert add("needs_reviewer", by_url.title) == {"entry": "Some PR work"}
    assert add("needs_reviewer", "Do y") == {
        "entry": "APP-3 Why not",
        "create": "1",
        "where": fresh.url,
    }
    assert add("needs_reviewer", "Plain") == {
        "entry": f"{no_key.url} Plain",
        "create": "1",
        "where": no_key.url,
    }
    assert add("jira_next", "APP-8 Next") == {
        "entry": "APP-8 Next",
        "create": "1",
        "where": "https://jira/browse/APP-8",
    }
    assert add("todos", "APP-1 Fix") == {"entry": "APP-1 Fix"}
    # Other people's PRs don't get one.
    review = next(i for i in panel(dashboard, "reviews_for_me").items)
    assert [a.name for a in review.actions] == []


def test_naming_settings_drive_pings_labels_and_jira() -> None:
    settings: dict[str, object] = {
        "github": snapshot(
            mine=[pr(1, requested_users=["alice"], labels=["needs-team"], head="me/XY-1")]
        ),
        "jira": JiraSnapshot(
            issues=[
                issue("XY-1", status="Doing"),
                issue("XY-2", type="Outage", labels=["esc:vp"]),
                issue("XY-3", type="Defect"),
            ]
        ),
        "ping_first": "Please look at {url}",
        "ping_follow_up": "Bump!",
        "placeholder_labels": ["needs-team"],
        "jira_statuses": {"in_progress": "Doing", "in_review": "Checking", "closed": "Shipped"},
        "escalation_label_prefix": "esc:",
        "escalation_levels": ["lead", "vp"],
        "incident_types": ["outage"],
        "bug_types": ["defect"],
    }
    dashboard = build(**settings)
    [target] = pings(**settings)[pr(1).url].targets
    assert target.message == f"Please look at {pr(1).url}"
    assert titles(dashboard, "metadata") == [pr(1).title]
    assert "needs-team" in panel(dashboard, "metadata").items[0].details[0]
    [transition] = panel(dashboard, "jira_transitions").items
    assert transition.subtitle == "Doing → Checking: all PRs open and non-draft"
    by_key = {i.title.split()[0]: i.badges[0] for i in panel(dashboard, "jira_next").items}
    assert by_key["XY-2"].startswith("incident, escalated (2)")  # vp is the 2nd level
    assert by_key["XY-3"].startswith("bug")
