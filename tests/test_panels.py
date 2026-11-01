"""Core's panels and the Dashboard: todos, on ice, nudges, groups and focus mode."""

from datetime import UTC, datetime

from conftest import (
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
from move_forward.page import Group
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
    items = panel(dashboard, "notes.todos").items
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
    assert dashboard.panel("github.feedback").key == "github.feedback"


def test_build_with_nothing_stored() -> None:
    dashboard = build()
    assert all(p.items == [] for p in dashboard.panels)
    assert dashboard.generated_at == datetime(2026, 10, 30, 12, tzinfo=UTC)


def test_groups_and_focus_follow_priority_with_act_now_first() -> None:
    dashboard = build(local=local_snapshot(), stacks=stacks(StackBranch(name="main")))
    assert [(g.key, g.panel_keys) for g in dashboard.groups] == [
        ("act", ["act.now"]),
        ("notes", ["notes.todos", "notes.iced"]),  # by each group's most urgent panel: step 0b
        (
            "github",
            [
                f"github.{k}"
                for k in (
                    "reviews_for_me",
                    "conflicts",
                    "feedback",
                    "ci_failures",
                    "metadata",
                    "needs_reviewer",
                    "team_prs",
                    "ready_to_merge",
                    "held",
                )
            ],
        ),
        ("git", ["git.local_work", "git.cleanup"]),
        ("git_stack", ["git_stack.stacks"]),
    ]
    everything = dashboard.focus_items(panels.FOCUS_ALL)
    assert everything is not None  # every item's panel, in the page's order; all empty here
    assert dashboard.focus_items("github") == []


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
    assert (
        keys.index("github.reviews_for_me")
        < keys.index("github.conflicts")
        < keys.index("github.needs_reviewer")
    )
    assert [i.title for _, i in everything if _.key == "github.reviews_for_me"] == [pr(3).title]
    act = dashboard.focus_items("act")  # the review request, then the conflicted PR
    assert act is not None and [i.title for _, i in act] == [
        f"Review: {pr(3).title}",  # quick, and its author is waiting
        f"Ping the reviewers: {pr(1).title}",  # quick
        f"Resolve conflicts: {pr(2).title}",  # about an hour
    ]
    one = dashboard.focus_items("slack.nudges")
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
    assert titles(dashboard, "notes.todos") == [
        "APP-2 mixed",
        "APP-3 stuck",
    ]  # the page is unchanged
    focused = dashboard.focus_items("notes.todos")
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
    focused = dashboard.focus_items("notes.todos")
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
    # The entry on the page gets the Add todo field and Put on ice, not the todo's own actions.
    assert [a.name for a in panel(dashboard, "notes.todos").items[0].actions] == [
        "notes/todo_add",
        "notes/ice_item",
    ]

    config, db = stored(work_items=[noted])
    kept = kept_item(config, db, f"notes:{todo_id}")
    assert kept is not None
    panel_key, done = kept
    assert panel_key == "notes.todos"
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
    focused = build(work_items=[noted], github=github).focus_items("notes.todos")
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
    focused = dashboard.focus_items("notes.todos")
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
    [(_, part)] = build(work_items=[noted]).focus_items("notes.todos") or []
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

    assert add("github.needs_reviewer", covered.title) == {"entry": "APP-1 Fix"}
    assert add("github.needs_reviewer", by_url.title) == {"entry": "Some PR work"}
    assert add("github.needs_reviewer", "Do y") == {
        "entry": "APP-3 Why not",
        "create": "1",
        "where": fresh.url,
    }
    assert add("github.needs_reviewer", "Plain") == {
        "entry": f"{no_key.url} Plain",
        "create": "1",
        "where": no_key.url,
    }
    assert add("jira.jira_next", "APP-8 Next") == {
        "entry": "APP-8 Next",
        "create": "1",
        "where": "https://jira/browse/APP-8",
    }
    assert add("notes.todos", "APP-1 Fix") == {"entry": "APP-1 Fix"}
    # Other people's PRs don't get one.
    review = next(i for i in panel(dashboard, "github.reviews_for_me").items)
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
    assert titles(dashboard, "github.metadata") == [pr(1).title]
    assert "needs-team" in panel(dashboard, "github.metadata").items[0].details[0]
    [transition] = panel(dashboard, "jira.jira_transitions").items
    assert transition.subtitle == "Doing → Checking: all PRs open and non-draft"
    by_key = {i.title.split()[0]: i.badges[0] for i in panel(dashboard, "jira.jira_next").items}
    assert by_key["XY-2"].startswith("incident, escalated (2)")  # vp is the 2nd level
    assert by_key["XY-3"].startswith("bug")


def test_panels_carry_the_skills_step_order_and_whether_they_need_acting_on_now() -> None:
    built = dashboard(
        github=snapshot(),
        local=local_snapshot(),
        stacks=stacks(),
        jira=JiraSnapshot(issues=[]),
        slack=True,
    )
    plugins_panels = [p for p in built.panels if p.key != "act.now"]
    by_priority = [p.key for p in sorted(plugins_panels, key=lambda p: p.priority)]
    assert by_priority == [
        "notes.todos",
        "github.reviews_for_me",
        "github.conflicts",
        "github.feedback",
        "github.ci_failures",
        "git.local_work",
        "git_stack.stacks",
        "github.metadata",
        "github.needs_reviewer",
        "github.team_prs",
        "slack.nudges",
        "github.ready_to_merge",
        "github.held",
        "git.cleanup",
        "jira.jira_transitions",
        "jira.jira_next",
        "notes.iced",
    ]
    assert {p.key for p in built.panels if p.act_now} == {
        "github.reviews_for_me",
        "github.feedback",
        "github.ci_failures",
        "github.conflicts",
        "github.ready_to_merge",
        "jira.jira_transitions",
        "git.cleanup",
        "slack.nudges",
    }


def test_a_layout_orders_groups_and_panels_with_act_now_first_and_new_ones_in_place() -> None:
    act = Group(key="act", title="Act now", panel_keys=["a.x", "a.y"], open=True)
    defaults = [
        act,
        Group(key="notes", title="Notes", panel_keys=["notes.todos", "notes.iced"]),
        Group(key="git", title="Git", panel_keys=["git.local_work", "git.cleanup"]),
        Group(key="jira", title="Jira", panel_keys=["jira.next"]),
    ]
    layout = panels.Layout(
        groups=["git", "act", "notes"],  # jira is new to it; Act now stays first anyway
        panels={"act": ["a.y", "a.x"], "git": ["git.cleanup"]},  # local_work is new
    )
    laid = panels.laid_out(defaults, layout)
    assert [g.key for g in laid] == ["act", "git", "jira", "notes"]  # jira after git, as before
    assert [g.panel_keys for g in laid] == [
        ["a.y", "a.x"],
        ["git.local_work", "git.cleanup"],  # first by default, so first still
        ["jira.next"],
        ["notes.todos", "notes.iced"],
    ]
    assert panels.laid_out(defaults, panels.Layout()) == defaults


def test_a_close_out_entry_is_nominated_to_finish() -> None:
    built = dashboard(
        work_items=[
            entry("APP-1 Almost", [todo("tick the box", "quick")]),
            entry("APP-2 Big", [todo("write it", "big")]),
            entry("APP-3 Stuck", [todo("ship", "quick")], **{"Blocked on": ["infra"]}),
        ]
    )
    act = built.act_now()
    assert [(i.title, i.details[0]) for i in act] == [
        ("tick the box: APP-1 Almost", "why: quick, closes APP-1 Almost")
    ]


def test_empty_notes_say_how_to_fill_them() -> None:
    empty = dashboard().panel("notes.todos")
    assert "mfd notes import <dir>" in empty.description
    some = dashboard(work_items=[entry("APP-1", [todo("x")])]).panel("notes.todos")
    assert "mfd notes import" not in some.description
