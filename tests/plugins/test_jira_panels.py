"""The Jira plugin's panels, built as core builds the page."""

from datetime import timedelta

from conftest import (
    NOW,
    approved_review,
    dashboard,
    issue,
    panel,
    pr,
    snapshot,
    stored,
    thread,
    titles,
)

from move_forward import refresh
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.refresh import build_dashboard

build = dashboard


def test_jira_transitions_from_pr_states() -> None:
    merged = pr(1, state="MERGED", merged_at=NOW)
    open_pr = pr(2)
    draft = pr(3, is_draft=True)
    dashboard = build(
        github=snapshot(mine=[open_pr, draft], merged_recent=[merged]),
        jira=JiraSnapshot(
            issues=[
                issue("APP-1", status="In Review"),
                issue("APP-2", status="In Progress"),
                issue("APP-3", status="In Review"),
                issue("APP-4", status="In Progress"),
                issue("APP-5", status="Accepted", status_category="new"),
            ]
        ),
    )
    subtitles = {
        i.title.split()[0]: i.subtitle for i in panel(dashboard, "jira.jira_transitions").items
    }
    assert subtitles == {
        "APP-1": "In Review → Closed: all PRs merged",
        "APP-2": "In Progress → In Review: all PRs open and non-draft",
        "APP-3": "In Review → In Progress: a PR is draft or has changes requested",
    }


def test_a_transition_with_a_target_status_has_a_button_to_make_it() -> None:
    dashboard = build(
        github=snapshot(mine=[pr(2)]),
        jira=JiraSnapshot(issues=[issue("APP-2", status="In Progress")]),
    )
    buttons = {
        i.title.split()[0]: [(a.name, a.label, a.fields) for a in i.actions]
        for i in panel(dashboard, "jira.jira_transitions").items
    }
    assert buttons["APP-2"][0] == (
        "jira/transition",
        "Move to In Review",
        {"key": "APP-2", "to": "In Review"},
    )


def test_jira_next_ranks_incidents_then_bugs_then_unblocked_work() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[
                issue("APP-1", type="Task"),
                issue("APP-2", type="Task", open_blockers=["APP-9"]),
                issue("APP-3", type="Bug"),
                issue("APP-4", type="Incident"),
                issue("APP-5", type="Incident", labels=["esc-vp"]),
                issue("APP-6", type="Bug", labels=["esc-manager"]),
            ]
        ),
        escalation_label_prefix="esc-",
        escalation_levels=["manager", "director", "vp"],
    )
    keys = [i.title.split()[0] for i in panel(dashboard, "jira.jira_next").items]
    assert keys == ["APP-5", "APP-4", "APP-6", "APP-3", "APP-1", "APP-2"]
    badges = {i.title.split()[0]: i.badges for i in panel(dashboard, "jira.jira_next").items}
    assert badges["APP-5"] == ["incident, escalated (3)"]
    assert badges["APP-2"] == ["blocked by APP-9"]


def test_a_ticket_whose_prs_merged_is_nominated_to_close() -> None:
    dashboard = build(
        github=snapshot(merged_recent=[pr(1, state="MERGED", merged_at=NOW)]),
        jira=JiraSnapshot(issues=[issue("APP-1", status="In Review")]),
    )
    [closing] = [i for i in dashboard.act_now() if "APP-1" in i.title]
    assert closing.title == "Move to Closed: APP-1 Do the thing"
    assert closing.details[0] == "why: quick, closes APP-1"


def test_incidents_escalated_bugs_and_tickets_due_this_week_are_nominated() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[
                issue("APP-1", type="Task"),
                issue("APP-2", type="Task", due=(NOW + timedelta(days=3)).date()),
                issue("APP-3", type="Task", due=(NOW + timedelta(days=30)).date()),
                issue("APP-5", type="Incident", labels=["esc-vp"]),
                issue("APP-6", type="Bug", labels=["esc-manager"]),
                issue("APP-7", type="Bug"),
                issue("APP-8", type="Incident", open_blockers=["APP-9"]),
            ]
        ),
        escalation_label_prefix="esc-",
        escalation_levels=["manager", "director", "vp"],
    )
    why = {i.title.split(": ", 1)[1].split()[0]: i.details[0] for i in dashboard.act_now()}
    assert why == {  # not APP-7, a bug not escalated; APP-8, blocked, ranks last, past 3 a tool
        "APP-5": "why: critical",
        "APP-6": "why: important",
        "APP-2": "why: due in 3d",  # the end of its day, local time (UTC in tests): 3d 12h
    }
    titles = [i.title for i in dashboard.act_now()]
    assert titles[0] == "Look at the incident: APP-5 Do the thing"
    next_badges = {i.title.split()[0]: i.badges for i in panel(dashboard, "jira.jira_next").items}
    assert next_badges["APP-2"] == ["unblocked", "due Nov 2"]


def test_a_transition_joins_its_prs_act_now_item_and_is_set_aside_with_it() -> None:
    mine = pr(2, threads=[thread("bob")])  # its branch names APP-2
    config, db = stored(
        github=snapshot(mine=[mine]),
        jira=JiraSnapshot(issues=[issue("APP-2", status="In Progress")]),
    )
    [item] = build_dashboard(config, db, NOW).act_now()
    assert item.title == f"Reply: {mine.title}"
    assert "then: Move to In Review: APP-2 Do the thing" in item.details
    snooze = next(a for a in item.actions if a.label == "Snooze")
    assert refresh.run_action(config, db, snooze.name, snooze.fields) == "snoozed until tomorrow"
    assert build_dashboard(config, db, NOW).act_now() == []  # the ticket's move too


def test_a_blocked_ticket_is_nominated_and_comes_back_once_untouched_for_days() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[
                issue(
                    "APP-1", type="Task", open_blockers=["APP-9"], updated=NOW - timedelta(days=1)
                ),
                issue(
                    "APP-2", type="Task", open_blockers=["APP-9"], updated=NOW - timedelta(days=5)
                ),
            ]
        )
    )
    why = {i.title.split(": ", 1)[1].split()[0]: i.details[0] for i in dashboard.act_now()}
    assert why == {"APP-1": "why: blocked", "APP-2": "why: blocked, untouched 5d"}
    assert dashboard.act_now()[0].title.startswith(
        "Chase the blockers: APP-2"
    )  # the stale one first


def test_any_name_of_a_status_matches_and_a_move_offers_them_in_order() -> None:
    dashboard = build(
        github=snapshot(mine=[pr(2)]),
        jira=JiraSnapshot(issues=[issue("APP-2", status="doing")]),
        jira_statuses={
            "in_progress": ["In Progress", "Doing"],
            "in_review": ["Code Review", "In Review"],
        },
    )
    [item] = panel(dashboard, "jira.jira_transitions").items
    assert item.subtitle == "doing → Code Review: all PRs open and non-draft"
    assert item.actions[0].fields == {"key": "APP-2", "to": "Code Review", "also": "In Review"}


def test_a_ticket_in_rollout_is_closed_by_hand_once_its_prs_merged() -> None:
    merged = pr(1, state="MERGED", merged_at=NOW)
    dashboard = build(
        github=snapshot(merged_recent=[merged]),
        jira=JiraSnapshot(
            issues=[issue("APP-1", status="Rollout"), issue("APP-3", status="Rollout")]
        ),  # APP-3 has no PRs: nothing to say
    )
    [item] = panel(dashboard, "jira.rollouts").items
    assert item.title.startswith("APP-1")
    assert item.details == ["all PRs merged; close once monitoring is done"]
    assert [(a.label, a.fields["to"]) for a in item.actions if a.name == "jira/transition"] == [
        ("Close", "Closed")
    ]
    assert item.signals and item.signals.verb == "Finish the rollout"
    assert item.signals.importance == -1 and item.signals.closes is None
    assert "Finish the rollout: APP-1 Do the thing" in [i.title for i in dashboard.act_now()]


def test_a_rollout_with_open_prs_lists_them_and_rises_when_one_is_ready_to_merge() -> None:
    merged = pr(1, state="MERGED", merged_at=NOW)
    ready = pr(2, reviews=[approved_review()], review_decision="APPROVED")
    waiting = pr(3, review_decision="REVIEW_REQUIRED")
    draft = pr(4, is_draft=True)
    dashboard = build(
        github=snapshot(mine=[ready, waiting, draft], merged_recent=[merged]),
        jira=JiraSnapshot(
            issues=[issue("APP-2", status="Rollout"), issue("APP-3", status="Rollout")]
        ),
    )
    assert titles(dashboard, "jira.jira_transitions") == []  # rollouts are their own panel
    items = {i.title.split()[0]: i for i in panel(dashboard, "jira.rollouts").items}
    assert [(l.text, l.url) for l in items["APP-2"].links] == [("ready to merge #2", ready.url)]
    assert items["APP-2"].details == []  # the links say where each PR is
    assert items["APP-2"].signals and items["APP-2"].signals.verb == "Advance the rollout"
    assert (items["APP-2"].signals.importance, items["APP-2"].signals.effort) == (0, "minutes")
    assert [l.text for l in items["APP-3"].links] == ["in review #3"]
    assert items["APP-3"].signals and items["APP-3"].signals.importance == -1
    # Act now joins it to the PR's own Merge item, not a second line
    titles_now = [i.title for i in dashboard.act_now()]
    assert titles_now == ["Merge: PR 2", "Advance the rollout: APP-3 Do the thing"]
    merge = next(i for i in dashboard.act_now() if i.title.startswith("Merge"))
    assert any("Advance the rollout" in d for d in merge.details)


def test_what_next_ranks_a_rollout_below_unblocked_work_and_never_nominates_it() -> None:
    dashboard = build(
        jira=JiraSnapshot(
            issues=[issue("APP-1", status="Rollout"), issue("APP-2", status="Accepted")]
        )
    )
    items = panel(dashboard, "jira.jira_next").items
    ranked = [i.title.split()[0] for i in sorted(items, key=lambda i: -i.rank)]
    assert ranked == ["APP-2", "APP-1"]
    assert {i.title.split()[0]: i.signals is None for i in items} == {"APP-1": True, "APP-2": False}
    assert items[0].badges[0] == "in rollout" or items[1].badges[0] == "in rollout"


def test_a_ticket_whose_only_pr_was_closed_unmerged_is_flagged() -> None:
    closed_review = pr(1, state="CLOSED")
    closed_progress = pr(2, state="CLOSED")
    merged_too = pr(3, state="MERGED", merged_at=NOW)
    closed_with_merged = pr(4, state="CLOSED")
    dashboard = build(
        github=snapshot(
            closed_recent=[closed_review, closed_progress, closed_with_merged],
            merged_recent=[merged_too],
        ),
        jira=JiraSnapshot(
            issues=[
                issue("APP-1", status="In Review"),
                issue("APP-2", status="In Progress"),
                issue("APP-3", status="In Progress"),
                issue("APP-4", status="Closed"),
            ]
        ),
    )
    items = {i.title.split()[0]: i for i in panel(dashboard, "jira.jira_transitions").items}
    assert items["APP-1"].subtitle == "PR #1 closed unmerged: reopen it or move the ticket back"
    assert [a.fields["to"] for a in items["APP-1"].actions if a.name == "jira/transition"] == [
        "In Progress"
    ]
    assert items["APP-2"].subtitle.startswith("PR #2 closed unmerged")
    assert not [a for a in items["APP-2"].actions if a.name == "jira/transition"]
    assert "APP-3" not in items  # its PR merged: nothing to reopen
