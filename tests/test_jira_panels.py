"""The Jira plugin's panels, built as core builds the page."""

from conftest import (
    NOW,
    dashboard,
    issue,
    panel,
    pr,
    snapshot,
)

from move_forward.plugins.jira.models import JiraSnapshot

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
    subtitles = {i.title.split()[0]: i.subtitle for i in panel(dashboard, "jira_transitions").items}
    assert subtitles == {
        "APP-1": "In Review → Closed: all PRs merged",
        "APP-2": "In Progress → In Review: all PRs open and non-draft",
        "APP-3": "In Review → In Progress: a PR is draft or has changes requested",
    }


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
    keys = [i.title.split()[0] for i in panel(dashboard, "jira_next").items]
    assert keys == ["APP-5", "APP-4", "APP-6", "APP-3", "APP-1", "APP-2"]
    badges = {i.title.split()[0]: i.badges for i in panel(dashboard, "jira_next").items}
    assert badges["APP-5"] == ["incident, escalated (3)"]
    assert badges["APP-2"] == ["blocked by APP-9"]
