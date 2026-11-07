"""Core's panels and the Dashboard: todos, on ice, nudges, groups and focus mode."""

import json
from datetime import UTC, datetime

import pytest
from conftest import (
    NOW,
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

from move_forward import panels, render
from move_forward.graph import Ref
from move_forward.page import Action, Badge, Group, Item, Panel, Signals
from move_forward.plugins.git_stack.models import StackBranch
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.notes.models import Ice, IcedItem
from move_forward.plugins.notes.panels import ADD_TODO_INPUT
from move_forward.refresh import kept_item

build = dashboard

BLOCKED_ON_REVIEWS = Badge(text="blocked: reviews", tone="warn")
"""An entry's badge, when it's blocked on reviews."""


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
    assert [part.title for part in items[2].parts] == ["small", "big one"]
    assert items[3].badges == ["close-out: 1 quick todo(s)", BLOCKED_ON_REVIEWS]


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


def test_a_panel_can_sort_assigned_importance_before_its_own_rank() -> None:
    ordinary = Item(title="ordinary", rank=100, subjects=[Ref(kind="x", id="ordinary")])
    critical = Item(title="critical", rank=1, subjects=[Ref(kind="x", id="critical")])
    low = Item(title="low", rank=200, subjects=[Ref(kind="x", id="low")])
    stage = Panel(
        key="stage",
        title="Stage",
        description="",
        items=[ordinary, critical, low],
        sort_by_importance=True,
    )
    assigned = {"critical": 2, "low": 0}
    built = panels.build(
        NOW,
        [panels.PluginPanels("x", "X", [stage])],
        {},
        priority_of=lambda ref: assigned.get(ref.id),
    )
    assert [item.title for item in built.panel("x.stage").items] == ["critical", "ordinary", "low"]


def test_every_entity_item_and_focus_part_gets_importance_choices() -> None:
    primary = Ref(kind="git.branch", id="branch")
    secondary = Ref(kind="pull_request", id="pr")
    part = Ref(kind="notes.todo", id="7")
    stage = Panel(
        key="stage",
        title="Stage",
        description="",
        items=[
            Item(
                title="Branch",
                subjects=[primary, secondary],
                parts=[Item(title="Todo", subjects=[part])],
            ),
            Item(title="No entity"),
        ],
    )
    built = panels.build(
        NOW,
        [panels.PluginPanels("x", "X", [stage])],
        {},
        priority_of=lambda ref: 2 if ref == part else None,
    )
    branch, no_entity = built.panel("x.stage").items
    assert [a.fields for a in branch.actions] == [
        {"kind": primary.kind, "id": primary.id, "level": level} for level in ("0", "1", "2")
    ]
    assert no_entity.actions == []
    focused = built.focus_items("x.stage")
    assert focused is not None
    [(_, todo_part), (_, empty)] = focused
    assert [a.fields["kind"] for a in todo_part.actions] == [part.kind] * 3
    assert [a.label for a in todo_part.actions if a.selected] == ["Critical"]
    assert empty.actions == []


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
                    "fix_before_review",
                    "ready_for_review",
                    "reviews_for_me",
                    "replies_for_me",
                    "conflicts",
                    "feedback",
                    "ci_failures",
                    "policy",
                    "metadata",
                    "needs_reviewer",
                    "team_prs",
                    "ready_to_merge",
                    "held",
                    "merge_queue",
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
        ("x", "APP-3 stuck", ["unsized", BLOCKED_ON_REVIEWS]),
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
    assert [(a.name, a.checkbox, a.checked) for a in part.actions if a.group != "Importance"] == [
        ("notes/todo_done", True, False),
        ("notes/todo_delete", False, False),
        ("notes/todo_link", False, False),
        ("notes/todo_size", False, False),
        ("notes/todo_size", False, False),
        ("notes/todo_add", False, False),
    ]
    todo_id = part.actions[0].fields["todo"]
    assert part.actions[0].fields == {"todo": todo_id}  # addressed by id
    assert part.actions[1].confirm is not None and "small" in part.actions[1].confirm
    assert [a.fields for a in part.actions if a.group == "Importance"] == [
        {"kind": "notes.todo", "id": todo_id, "level": level} for level in ("0", "1", "2")
    ]
    # A single-todo entry can be completed on the dashboard as well as in Focus mode.
    assert [
        a.name for a in panel(dashboard, "notes.todos").items[0].actions if a.group != "Importance"
    ] == [
        "notes/todo_done",
        "notes/todo_delete",
        "notes/todo_link",
        "notes/todo_size",
        "notes/todo_size",
        "notes/todo_add",
        "notes/ice_item",
    ]
    assert (
        render.item_html(panel(dashboard, "notes.todos").items[0]).count(
            "action='/actions/notes/todo_done'"
        )
        == 1
    )

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


def test_notes_panel_shows_each_todo_with_its_done_checkbox() -> None:
    noted = entry("APP-2 mixed", [todo("small", "quick"), todo("big one", "big")])
    dashboard = build(work_items=[noted])
    [item] = panel(dashboard, "notes.todos").items
    html = render.item_html(item)
    assert "<ul class='item-parts'>" in html
    assert html.count("action='/actions/notes/todo_done'") == 2
    assert "small" in html and "big one" in html
    assert "[quick] small" not in html  # the rows say it, with their buttons


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
    assert [link.done for link in parts[0].links] == [True, False, True]  # merged, open, closed
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
    assert [(l.text, l.url, l.strong, l.done) for l in part.links] == [
        ("PR 4", "https://github.com/acme/other/pull/4", True, False),
        ("PR 7", "https://github.com/acme/widgets/pull/7", False, True),
        ("APP-9", "https://j.example/browse/APP-9", True, False),
    ]
    assert part.url == "https://github.com/acme/other/pull/4"
    # An unknown-state PR is not evidence the todo is done.
    assert part.actions[0].suggested is False
    assert [a.name for a in part.actions if a.group != "Importance"] == [
        "notes/todo_done",
        "notes/todo_delete",
        "notes/todo_link",
        "notes/todo_size",
        "notes/todo_size",
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
    # Other people's PRs get the snooze and the shared importance choices.
    review = next(i for i in panel(dashboard, "github.reviews_for_me").items)
    assert [a.name for a in review.actions if a.group != "Importance"] == ["act_now/snooze"]


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
        "jira_statuses": {
            "in_progress": ["Doing"],
            "in_review": ["Checking"],
            "closed": ["Shipped"],
        },
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
    by_key = {
        i.title.split()[0]: str(b if isinstance(b := i.badges[0], str) else b.text)
        for i in panel(dashboard, "jira.jira_next").items
    }
    assert by_key["XY-2"].startswith("incident, escalated (2)")  # vp is the 2nd level
    assert by_key["XY-3"].startswith("bug")


def test_a_long_blocked_on_badge_is_cut_at_a_word_with_an_ellipsis() -> None:
    reason = "waiting for the platform team to finish the migration of the billing service"
    built = dashboard(
        work_items=[
            entry("APP-1 Stuck", [todo("ship", "quick")], **{"Blocked on": [reason + " today"]})
        ]
    )
    [badge] = [
        b.text
        for b in panel(built, "notes.todos").items[0].badges
        if isinstance(b, Badge) and b.text.startswith("blocked")
    ]
    assert badge == f"blocked: {reason}…"  # " today" would cut a word at 80 characters
    assert len(badge) <= len("blocked: ") + 80


def test_a_ticket_in_the_blocked_status_is_blocked_without_blocker_links() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[
                issue("XY-1", status="Blocked"),
                issue("XY-2", status="Blocked", type="Incident"),
                issue("XY-3"),
            ]
        )
    )
    items = {i.title.split()[0]: i for i in panel(dashboard, "jira.jira_next").items}
    signals = {key: item.signals for key, item in items.items()}
    assert all(s is not None for s in signals.values())
    assert items["XY-1"].badges[0] == Badge(text="blocked", tone="warn")
    assert signals["XY-1"] and signals["XY-1"].blocked
    assert items["XY-2"].badges[0] == Badge(text="incident, blocked", tone="warn")
    assert signals["XY-2"]
    assert signals["XY-2"].blocked
    assert items["XY-3"].badges[0] == "unblocked" and signals["XY-3"]
    assert not signals["XY-3"].blocked


def test_incidents_and_bugs_at_equal_escalation_rank_by_jira_priority() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[
                issue("XY-1", type="Incident", priority="Low"),
                issue("XY-2", type="Incident", priority="Highest"),
                issue("XY-3", type="Incident", priority="High"),
                issue("XY-4", type="Bug", priority="Highest"),
                issue("XY-5", type="Bug", priority="Low"),
            ]
        )
    )
    items = panel(dashboard, "jira.jira_next").items
    assert [i.title.split()[0] for i in sorted(items, key=lambda i: -i.rank)] == [
        "XY-2",
        "XY-3",
        "XY-1",
        "XY-4",
        "XY-5",
    ]
    # Act now only gets a bump at the top priority
    assert all(i.signals is not None for i in items)
    assert {i.title.split()[0]: i.signals.importance for i in items if i.signals} == {
        "XY-1": 1,
        "XY-2": 2,
        "XY-3": 1,
        "XY-4": 1,
        "XY-5": 0,
    }


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
        "github.fix_before_review",
        "github.ready_for_review",
        "github.reviews_for_me",
        "github.replies_for_me",
        "github.conflicts",
        "github.feedback",
        "github.ci_failures",
        "github.policy",
        "git.local_work",
        "git_stack.stacks",
        "github.metadata",
        "github.needs_reviewer",
        "github.team_prs",
        "slack.nudges",
        "github.ready_to_merge",
        "github.held",
        "github.merge_queue",
        "git.cleanup",
        "jira.jira_transitions",
        "jira.rollouts",
        "jira.jira_next",
        "notes.iced",
    ]
    assert {p.key for p in built.panels if p.act_now} == {
        "github.reviews_for_me",
        "github.replies_for_me",
        "github.feedback",
        "github.ci_failures",
        "github.policy",
        "github.conflicts",
        "github.needs_reviewer",
        "github.ready_to_merge",
        "jira.jira_transitions",
        "jira.rollouts",
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
        ("tick the box: APP-1 Almost", "why: quick, closes APP-1 Almost"),
        ("ship: APP-3 Stuck", "why: blocked, quick, closes APP-3 Stuck"),  # ranked down, not out
    ]


def test_empty_notes_say_how_to_fill_them() -> None:
    empty = dashboard().panel("notes.todos")
    assert "mfd notes import <dir>" in empty.description
    some = dashboard(work_items=[entry("APP-1", [todo("x")])]).panel("notes.todos")
    assert "mfd notes import" not in some.description


def test_a_plugins_buttons_in_act_now_carry_the_items_fields_but_acts_own_do_not() -> None:
    action = Action(name="github/anything", label="Do it", fields={"pr": "1"})
    page = panels.build(
        NOW,
        [
            panels.PluginPanels(
                "github",
                "GitHub",
                [],
                [
                    Item(
                        title="x",
                        subjects=[Ref(kind="pull_request", id="p")],
                        signals=Signals(verb="Go", task="go"),
                        actions=[action],
                    )
                ],
            )
        ],
        {},
    )
    (item,) = page.act_now()
    ours = {a.label: a for a in item.actions}
    assert ours["Do it"].fields["opened"] == item.act_now_fields
    assert "opened" not in ours["Snooze"].fields
    assert item.act_now_fields is not None
    assert json.loads(item.act_now_fields)["kind"] == "pull_request"


def test_a_nomination_without_a_task_is_left_out_of_act_now_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def nominated(title: str, task: str) -> Item:
        return Item(
            title=title,
            subjects=[Ref(kind="pull_request", id=title)],
            signals=Signals(verb="Go", task=task),
        )

    page = panels.build(
        NOW,
        [panels.PluginPanels("linear", "Linear", [], [nominated("a", "go"), nominated("b", "")])],
        {},
    )
    assert [i.title for i in page.act_now()] == ["Go: a"]
    assert "linear nominated 'b' for Act now without a task" in caplog.text


def test_a_button_posting_to_no_declared_action_is_left_out_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def button(name: str) -> Action:
        return Action(name=name, label=name, fields={})

    item = Item(
        title="x",
        actions=[button("notes/todo_done"), button("notes/todo_dnoe"), button("act_now/pin")],
    )
    page = panels.build(
        NOW,
        [
            panels.PluginPanels(
                "notes", "Notes", [Panel(key="todos", title="Todos", description="", items=[item])]
            )
        ],
        {},
        known_action={"notes/todo_done"}.__contains__,
    )
    [shown] = page.panel("notes.todos").items
    assert [a.name for a in shown.actions] == ["notes/todo_done", "act_now/pin"]
    assert "a button posts to 'notes/todo_dnoe'" in caplog.text


def test_what_only_core_sets_on_an_item_is_cleared_from_a_plugins() -> None:
    forged = Item(
        title="x",
        subjects=[Ref(kind="pull_request", id="p")],
        act_now_fields='{"kind": "other", "id": "y", "task": "t", "fingerprint": "f"}',
        opened_at=NOW,
    )
    built = panels.build(
        NOW,
        [
            panels.PluginPanels(
                "notes", "Notes", [Panel(key="t", title="T", description="", items=[forged])]
            )
        ],
        {},
    )
    [shown] = built.panel("notes.t").items
    assert shown.act_now_fields is None and shown.opened_at is None


def test_what_a_lone_todo_links_shows_on_its_entry() -> None:
    url = "https://github.com/acme/widgets/pull/7"
    noted = entry("APP-1 one", [todo(f"check {url}", "quick")])
    built = dashboard(
        work_items=[noted],
        github=snapshot(merged_recent=[pr(7, state="MERGED", merged_at=NOW)]),
    )
    [item] = panel(built, "notes.todos").items
    assert any(link.url == url and link.strong for link in item.links)
    assert "its PRs are done" in item.badges
    done = next(a for a in item.actions if a.name == "notes/todo_done")
    assert done.suggested


def test_what_is_on_ice_has_no_importance_choices() -> None:
    iced = Ice(
        id=1,
        title="parked",
        iced_on=NOW.date(),
        items=[IcedItem(kind="pull_request", ref_id="https://x/1", name="x")],
    )
    [item] = panel(dashboard(iced_stacks=[iced]), "notes.iced").items
    assert not [a for a in item.actions if a.group == "Importance"]
