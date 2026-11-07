"""Requesting a reviewer for a PR that needs one, from the page and through Act now."""

import pytest
from conftest import ME, REPO, dashboard, panel, pr, snapshot, stored

from move_forward.page import Action, Item
from move_forward.plugin import ActionError
from move_forward.plugins.github import client
from move_forward.plugins.github.panels import REQUEST_REVIEWER
from move_forward.refresh import perform_action

URL = f"https://github.com/{REPO}/pull/1"


def _field(item: Item) -> Action | None:
    return next((a for a in item.actions if a.name == REQUEST_REVIEWER), None)


def test_a_pr_missing_a_reviewer_has_the_field_and_reaches_act_now() -> None:
    built = dashboard(github=snapshot(mine=[pr(1)]))
    assert _field(panel(built, "github.needs_reviewer").items[0]) is not None
    assert _field(panel(built, "github.fix_before_review").items[0]) is not None
    [item] = built.act_now()
    assert item.title == "Request a reviewer: PR 1"
    assert _field(item) is not None


def test_a_pr_with_something_else_to_do_is_not_nominated_for_a_reviewer() -> None:
    built = dashboard(github=snapshot(mine=[pr(1, mergeable="CONFLICTING")]))
    [item] = built.act_now()
    assert item.title == "Resolve conflicts: PR 1"
    assert not any("Request a reviewer" in detail for detail in item.details)


def test_a_pr_with_a_reviewer_has_no_field() -> None:
    built = dashboard(github=snapshot(mine=[pr(1, requested_users=["alice"])]))
    assert panel(built, "github.needs_reviewer").items == []
    assert _field(panel(built, "github.ready_for_review").items[0]) is None


def test_a_pr_failing_for_another_reason_has_no_field_in_fix_before_review() -> None:
    built = dashboard(
        github=snapshot(mine=[pr(1, requested_users=["alice"], mergeable="CONFLICTING")])
    )
    assert _field(panel(built, "github.fix_before_review").items[0]) is None


def test_it_suggests_a_requested_teams_members_but_not_who_is_asked_or_the_author() -> None:
    github = snapshot(
        mine=[pr(1, requested_users=["zed"], requested_teams=["core"])],
    )
    built = dashboard(github=github, default_team_reviewer_assignment="individual")
    field = _field(panel(built, "github.needs_reviewer").items[0])
    assert field is not None and field.suggestions == ["alice"]


def test_requesting_one_asks_github_and_fetches_the_pr_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str]) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(client, "run", run)
    config, db = stored(github=snapshot(mine=[pr(1)]))
    result = perform_action(config, db, REQUEST_REVIEWER, {"pr": URL, "input": "alice"})
    assert commands == [["gh", "pr", "edit", URL, "--add-reviewer", "alice"]]
    assert [ref.id for ref in result.touched] == [URL]


@pytest.mark.parametrize(
    ("url", "login", "says"),
    [
        (URL, "--repo=evil/x", "isn't a GitHub login"),
        (URL, ME.upper(), "author can't review"),
        (URL, "ALICE", "already asked"),
        ("https://github.com/other/repo/pull/9", "bob", "isn't one of my open PRs"),
    ],
)
def test_a_bad_request_is_refused_without_running_anything(
    monkeypatch: pytest.MonkeyPatch, url: str, login: str, says: str
) -> None:
    def run(args: list[str]) -> str:
        pytest.fail(f"ran {args}")

    monkeypatch.setattr(client, "run", run)
    config, db = stored(github=snapshot(mine=[pr(1, requested_users=["alice"])]))
    with pytest.raises(ActionError, match=says):
        perform_action(config, db, REQUEST_REVIEWER, {"pr": url, "input": login})


def test_a_queued_pr_is_not_listed() -> None:
    queued = pr(1, requested_teams=["core"], in_merge_queue=True)
    built = dashboard(github=snapshot(mine=[queued]), default_team_reviewer_assignment="individual")
    assert panel(built, "github.needs_reviewer").items == []


def test_an_approved_pr_is_listed_but_not_nominated() -> None:
    # Stacked, so it's not Ready to merge either: nothing else nominates it.
    approved = pr(1, requested_teams=["core"], review_decision="APPROVED", base="me/other")
    built = dashboard(
        github=snapshot(mine=[approved]), default_team_reviewer_assignment="individual"
    )
    assert [i.title for i in panel(built, "github.needs_reviewer").items] == ["PR 1"]
    assert built.act_now() == []


def test_a_team_whose_members_cannot_be_read_is_listed_but_not_nominated() -> None:
    unknown = pr(1, requested_teams=["elsewhere"])
    built = dashboard(
        github=snapshot(mine=[unknown]), default_team_reviewer_assignment="individual"
    )
    [item] = panel(built, "github.needs_reviewer").items
    assert item.details == ["cannot verify members of elsewhere"]
    assert built.act_now() == []
