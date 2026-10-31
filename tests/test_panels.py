"""Core's panels and the Dashboard: todos, on ice, nudges, groups and focus mode."""

from datetime import UTC, datetime, timedelta

from conftest import (
    NOW,
    REPO,
    approved_review,
    dashboard,
    event,
    issue,
    local_snapshot,
    panel,
    pr,
    snapshot,
    thread,
    titles,
    work_item,
)

from move_forward import panels
from move_forward.domain import (
    Check,
    JiraSnapshot,
    LocalRepo,
    LocalSnapshot,
    Ping,
    ReviewerUser,
    StackBranch,
    TeamChannel,
    Todo,
)

build = dashboard


# Pings and nudges


def _teams() -> dict[str, TeamChannel]:
    return {"web": TeamChannel(slug="web", channel_name="#team-web", channel_id="C1")}


def _users() -> dict[str, ReviewerUser]:
    return {"alice": ReviewerUser(login="alice", email="a@x", slack_id="U1")}


def test_ping_targets_links_and_messages() -> None:
    candidate = pr(1, requested_users=["alice", "bob"], requested_teams=["web", "unknown"])
    dashboard = build(
        github=snapshot(mine=[candidate]),
        users=_users(),
        teams=_teams(),
        slack_workspace_url="https://acme.slack.com/",
    )
    ping = dashboard.pings[candidate.url]
    assert ping.blockers == []
    by_name = {t.name: t for t in ping.targets}
    assert by_name["alice"].slack_link == "https://acme.slack.com/team/U1"
    assert by_name["bob"].slack_link is None and by_name["bob"].destination == "bob (DM)"
    assert by_name["web"].slack_link == "https://acme.slack.com/archives/C1"
    assert by_name["web"].destination == "#team-web (C1)"
    assert by_name["unknown"].destination == "team unknown"
    assert by_name["alice"].message == panels.FIRST_PING.format(url=candidate.url)


def test_follow_up_after_a_recorded_ping_and_skill_nudges_count_for_all_targets() -> None:
    candidate = pr(1, requested_users=["alice"], requested_teams=["web"])
    other = pr(2, requested_users=["alice"])
    dashboard = build(
        github=snapshot(mine=[candidate, other]),
        pings=[
            Ping(
                pr_url=candidate.url,
                target="user:alice",
                kind="first",
                sent_at=NOW - timedelta(days=1),
            ),
            Ping(pr_url=other.url, target="unknown (from work notes)", kind="first", sent_at=NOW),
        ],
    )
    targets = {t.name: t for t in dashboard.pings[candidate.url].targets}
    assert targets["alice"].ping_kind == "follow-up"
    assert targets["alice"].message == panels.FOLLOW_UP_PING
    assert targets["web"].ping_kind == "first"
    assert dashboard.pings[other.url].targets[0].ping_kind == "follow-up"


def test_nudge_blockers_and_staleness() -> None:
    fresh = pr(
        1,
        requested_users=["alice"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(hours=2), reviewer="alice")],
    )
    stale = pr(
        2,
        requested_users=["alice"],
        events=[event("ReviewRequestedEvent", NOW - timedelta(days=2), reviewer="alice")],
    )
    policy_only = pr(
        3,
        requested_teams=["web"],
        checks=[Check(name="review-policy/owners", state="FAILURE")],
    )
    broken = pr(4, requested_users=["alice"], checks=[Check(name="tests", state="FAILURE")])
    threads = pr(5, requested_users=["alice"], threads=[thread()])
    approved = pr(6, requested_users=["alice"], reviews=[approved_review()])
    nobody = pr(7)
    draft = pr(8, is_draft=True, requested_users=["alice"])
    dashboard = build(
        github=snapshot(mine=[fresh, stale, policy_only, broken, threads, approved, nobody, draft]),
        review_checks=["review-policy/owners"],
    )
    pings = dashboard.pings
    assert pings[fresh.url].blockers == [] and not pings[fresh.url].stale
    assert pings[stale.url].stale
    assert pings[policy_only.url].blockers == []
    assert pings[broken.url].blockers == ["failing checks"]
    assert pings[threads.url].blockers == ["unresolved threads"]
    assert pings[approved.url].blockers == ["already approved"]
    assert pings[nobody.url].blockers == ["no reviewers requested"]
    assert draft.url not in pings
    assert sorted(titles(dashboard, "nudges")) == ["PR 2", "PR 3"]


def test_todos_put_close_outs_first_and_blocked_last() -> None:
    close_out = work_item("APP-1 almost", [Todo(text="reply", size="quick")])
    mixed = work_item(
        "APP-2 mixed", [Todo(text="big one", size="big"), Todo(text="small", size="quick")]
    )
    blocked = work_item(
        "APP-3 stuck", [Todo(text="x", size="quick")], **{"Blocked on": ["reviews"]}
    )
    unblocked = work_item(
        "APP-4 fine", [Todo(text="x", size="quick")], **{"Blocked on": ["nothing"]}
    )
    done = work_item("APP-5 done", [])
    dashboard = build(work_items=[mixed, blocked, close_out, done, unblocked])
    items = panel(dashboard, "todos").items
    assert [i.title for i in items] == ["APP-1 almost", "APP-4 fine", "APP-2 mixed", "APP-3 stuck"]
    assert items[0].badges == ["close-out: 1 quick todo(s)"]
    assert items[2].details == ["[quick] small", "[big] big one"]
    assert items[3].badges == ["close-out: 1 quick todo(s)", "blocked: reviews"]


def test_every_panel_is_in_exactly_one_group() -> None:
    dashboard = build(
        local=local_snapshot(stack=[StackBranch(name="main")]), jira=JiraSnapshot(issues=[])
    )
    grouped = [key for group in dashboard.groups for key in group.panel_keys]
    assert sorted(grouped) == sorted(p.key for p in dashboard.panels)
    assert dashboard.panel("feedback").key == "feedback"


def test_age() -> None:
    assert panels.age(NOW, NOW - timedelta(minutes=5)) == "5m"
    assert panels.age(NOW, NOW - timedelta(hours=5)) == "5h"
    assert panels.age(NOW, NOW - timedelta(days=5)) == "5d"
    assert panels.age(NOW, None) == "?"
    assert panels.age(NOW, datetime(2026, 10, 29, 12)) == "1d"  # naive counts as UTC


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
                    stack=[StackBranch(name="main")],
                )
            ]
        )
    )
    assert {p.key for p in dashboard.panels} <= set(panels.FOCUS_ORDER)
    assert len(set(panels.FOCUS_ORDER)) == len(panels.FOCUS_ORDER)
    assert panels.FOCUS_ORDER.index("reviews_for_me") < panels.FOCUS_ORDER.index("feedback")
    assert panels.FOCUS_ORDER.index("cleanup") < panels.FOCUS_ORDER.index("iced")


def test_focus_items_by_category() -> None:
    dashboard = build(
        github=snapshot(
            mine=[pr(1, requested_users=["alice"]), pr(2, merge_state="DIRTY")],
            direct_requests=[pr(3, author="bob")],
        )
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
    mixed = work_item(
        "APP-2 mixed",
        [Todo(text="big one", size="big"), Todo(text="small", size="quick")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    blocked = work_item("APP-3 stuck", [Todo(text="x")], **{"Blocked on": ["reviews"]})
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


def test_todo_parts_carry_their_actions_and_a_done_todo_can_be_rebuilt() -> None:
    entry = work_item(
        "APP-2 mixed",
        [Todo(text="small", size="quick")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    dashboard = build(work_items=[entry])
    focused = dashboard.focus_items("todos")
    assert focused is not None
    [(_, part)] = focused
    assert [(a.name, a.checkbox, a.checked) for a in part.actions] == [
        ("todo_done", True, False),
        ("todo_delete", False, False),
        ("todo_link", False, False),
        ("todo_add", False, False),
    ]
    assert part.actions[0].fields == {"entry": "APP-2 mixed", "text": "small"}
    assert part.actions[1].confirm is not None and "small" in part.actions[1].confirm
    # The entry on the page only gets the Add todo field, not the todo's own actions.
    assert [a.name for a in panel(dashboard, "todos").items[0].actions] == ["todo_add"]

    done = dashboard.done_todo("APP-2 mixed", "small")
    assert (done.title, done.subtitle, done.badges) == ("small", "APP-2 mixed", ["done"])
    assert done.url == "https://github.com/acme/widgets/pull/7"
    assert [(a.name, a.checked) for a in done.actions] == [("todo_undone", True)]
    assert dashboard.done_todo("gone entry", "x").url is None


def test_todo_parts_put_the_prs_they_mention_first_in_bold() -> None:
    urls = [f"https://github.com/acme/widgets/pull/{n}" for n in (11, 222, 3333)]
    entry = work_item(
        "APP-2",
        [
            Todo(text="get it merged"),
            Todo(text="reply on #222 then rebase 3333"),
            Todo(text=f"check {urls[0]} plans; 1222 is not a PR"),
            Todo(text="follow up on 3333"),
        ],
        Where=urls,
    )
    github = snapshot(
        mine=[pr(222)], merged_recent=[pr(11, state="MERGED")], closed_recent=[pr(3333)]
    )
    focused = build(work_items=[entry], github=github).focus_items("todos")
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
    entry = work_item(
        "APP-2 thing",
        [Todo(text="ask about APP-9 and APP-9 again https://github.com/acme/other/pull/4")],
        Where=["https://github.com/acme/widgets/pull/7"],
    )
    github = snapshot(merged_recent=[pr(7, state="MERGED")])
    dashboard = build(work_items=[entry], github=github, jira_site="https://j.example/")
    focused = dashboard.focus_items("todos")
    assert focused is not None
    [(_, part)] = focused
    assert [(l.text, l.url, l.strong, l.pr_state) for l in part.links] == [
        ("PR 7", "https://github.com/acme/widgets/pull/7", False, "merged"),
        ("PR 4", "https://github.com/acme/other/pull/4", True, None),
        ("APP-9", "https://j.example/browse/APP-9", True, None),
    ]
    assert part.url == "https://github.com/acme/other/pull/4"
    # An unknown-state PR is not evidence the todo is done.
    assert part.actions[0].suggested is False
    assert [a.name for a in part.actions] == ["todo_done", "todo_delete", "todo_link", "todo_add"]
    assert part.actions[2].input == "Jira key or PR URL"
    # Without a Jira site, keys aren't linked.
    [(_, part)] = build(work_items=[entry]).focus_items("todos") or []
    assert [l.text for l in part.links] == ["PR 7", "PR 4"]


def test_my_prs_tickets_and_entries_get_an_add_todo_field() -> None:
    covered = pr(1, head="me/APP-1-x")  # APP-1 has an entry
    by_url = pr(2, head="me/no-key")
    fresh = pr(3, head="me/APP-3-y", title="Do y")
    no_key = pr(4, head="me/plain", title="Plain", body="no ticket")
    entries = [
        work_item("APP-1 Fix", [Todo(text="t")]),
        work_item("Some PR work", [Todo(text="u")], Where=[by_url.url]),
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
        [action] = [a for a in item.actions if a.name == "todo_add"]
        assert action.input == panels.ADD_TODO_INPUT
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
    dashboard = build(
        github=snapshot(
            mine=[pr(1, requested_users=["alice"], labels=["needs-team"], head="me/XY-1")]
        ),
        jira=JiraSnapshot(
            issues=[
                issue("XY-1", status="Doing"),
                issue("XY-2", type="Outage", labels=["esc:vp"]),
                issue("XY-3", type="Defect"),
            ]
        ),
        ping_first="Please look at {url}",
        ping_follow_up="Bump!",
        placeholder_labels=["needs-team"],
        jira_statuses={"in_progress": "Doing", "in_review": "Checking", "closed": "Shipped"},
        escalation_label_prefix="esc:",
        escalation_levels=["lead", "vp"],
        incident_types=["outage"],
        bug_types=["defect"],
    )
    [target] = dashboard.pings[pr(1).url].targets
    assert target.message == f"Please look at {pr(1).url}"
    assert titles(dashboard, "metadata") == [pr(1).title]
    assert "needs-team" in panel(dashboard, "metadata").items[0].details[0]
    [transition] = panel(dashboard, "jira_transitions").items
    assert transition.subtitle == "Doing → Checking: all PRs open and non-draft"
    by_key = {i.title.split()[0]: i.badges[0] for i in panel(dashboard, "jira_next").items}
    assert by_key["XY-2"].startswith("incident, escalated (2)")  # vp is the 2nd level
    assert by_key["XY-3"].startswith("bug")
