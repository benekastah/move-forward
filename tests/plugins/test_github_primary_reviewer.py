"""Picking a PR's primary reviewer: an assignee besides its author, in the repos that have one."""

import pytest
from conftest import ME, REPO, approved_review, dashboard, panel, pr, snapshot, stored

from move_forward.page import Item
from move_forward.plugin import ActionError
from move_forward.plugins.github import client
from move_forward.plugins.github.client import RawPullRequest
from move_forward.plugins.github.models import DerivedSnapshot, ReviewerSuggestion
from move_forward.plugins.github.panels import PRIMARY_REVIEWER
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.refresh import perform_action

URL = f"https://github.com/{REPO}/pull/1"


def _housekeeping_item(**overrides: object) -> Item:
    """PR 1 as PR housekeeping shows it, a draft being listed there in any repo."""
    settings: dict[str, object] = {"primary_reviewer_repos": [REPO]}
    settings.update(overrides)
    prs = settings.pop("prs", [pr(1, is_draft=True)])
    built = dashboard(github=snapshot(mine=prs), **settings)
    return next(i for i in panel(built, "github.metadata").items if i.url == URL)


def _field(item: Item):
    return next((a for a in item.actions if a.name == PRIMARY_REVIEWER), None)


def _run(monkeypatch: pytest.MonkeyPatch, login: str, *prs: object, **settings: object):
    """The commands the action runs on `prs` in a repo with primary reviewers."""
    commands: list[list[str]] = []

    def run(args: list[str]) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(client, "run", run)
    config, db = stored(
        github=snapshot(mine=list(prs)), **{"primary_reviewer_repos": [REPO], **settings}
    )
    result = perform_action(config, db, PRIMARY_REVIEWER, {"pr": URL, "input": login})
    return commands, result


def test_assignees_are_read_from_github_and_kept() -> None:
    raw = RawPullRequest.model_validate(
        {
            "number": 1,
            "title": "t",
            "url": URL,
            "isDraft": False,
            "state": "OPEN",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "headRefName": "h",
            "headRefOid": "a",
            "baseRefName": "main",
            "repository": {"nameWithOwner": REPO},
            "assignees": {"nodes": [{"login": ME}, {"login": "alice"}]},
        }
    )
    assert client.to_pull_request(raw).assignees == [ME, "alice"]
    _, db = stored(github=snapshot(mine=[pr(1, assignees=[ME, "alice"])]))
    loaded = PrRepository(db).load()
    assert loaded is not None and loaded.mine[0].assignees == [ME, "alice"]


def test_my_prs_in_a_listed_repo_get_the_field_and_others_do_not() -> None:
    assert _field(_housekeeping_item()) is not None
    assert _field(_housekeeping_item(primary_reviewer_repos=[])) is None
    assert _field(_housekeeping_item(primary_reviewer_repos=[REPO.upper()])) is not None


def test_the_field_is_on_every_item_about_the_pr_and_in_act_now() -> None:
    built = dashboard(
        github=snapshot(mine=[pr(1, mergeable="CONFLICTING")]), primary_reviewer_repos=[REPO]
    )
    assert _field(panel(built, "github.conflicts").items[0]) is not None
    assert _field(panel(built, "act.github").items[0]) is not None


def test_a_pr_in_the_merge_queue_has_no_field() -> None:
    built = dashboard(
        github=snapshot(mine=[pr(1, in_merge_queue=True)]), primary_reviewer_repos=[REPO]
    )
    assert _field(panel(built, "github.merge_queue").items[0]) is None


def test_the_current_primary_shows_but_never_the_author() -> None:
    assert (
        "primary: alice"
        in _housekeeping_item(prs=[pr(1, is_draft=True, assignees=[ME, "alice"])]).badges
    )
    item = _housekeeping_item(prs=[pr(1, is_draft=True, assignees=[ME])])
    assert not any(str(b).startswith("primary") for b in item.badges)
    field = _field(item)
    assert field is not None and field.label == "Set primary"


def test_suggestions_are_the_requested_then_team_members_then_committers() -> None:
    github = snapshot(
        mine=[
            pr(
                1,
                is_draft=True,
                requested_users=["zed", "alice"],
                requested_teams=["web"],
                assignees=[ME, "alice"],
            )
        ],
        team_memberships={f"{REPO.partition('/')[0]}/web": ["bob", ME, "zed"]},
    )
    derived = DerivedSnapshot(
        suggestions={
            URL: [
                ReviewerSuggestion(login="carol", commits=9),
                ReviewerSuggestion(login="bob", commits=1),
            ]
        }
    )
    built = dashboard(github=github, derived=derived, primary_reviewer_repos=[REPO])
    item = next(i for i in panel(built, "github.metadata").items if i.url == URL)
    field = _field(item)
    assert field is not None and field.suggestions == ["zed", "bob", "carol"]


def test_setting_one_assigns_them_and_asks_for_their_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands, result = _run(monkeypatch, "alice", pr(1, assignees=[ME]))
    assert commands == [
        ["gh", "pr", "edit", URL, "--add-assignee", "alice", "--add-reviewer", "alice"]
    ]
    assert "alice" in result.notice
    assert [ref.id for ref in result.touched] == [URL]


def test_a_new_one_replaces_the_old(monkeypatch: pytest.MonkeyPatch) -> None:
    commands, _ = _run(monkeypatch, "bob", pr(1, assignees=[ME, "alice"]))
    assert commands[0][4:] == [
        "--add-assignee",
        "bob",
        "--remove-assignee",
        "alice",
        "--add-reviewer",
        "bob",
    ]


@pytest.mark.parametrize(
    "already",
    [{"requested_users": ["ALICE"]}, {"reviews": [approved_review("alice")]}],
)
def test_someone_asked_or_who_reviewed_is_not_asked_again(
    monkeypatch: pytest.MonkeyPatch, already: dict[str, object]
) -> None:
    commands, _ = _run(monkeypatch, "alice", pr(1, **already))
    assert "--add-reviewer" not in commands[0]


@pytest.mark.parametrize(
    ("login", "assignees", "says"),
    [
        ("--repo=evil/x", [], "isn't a GitHub login"),
        ("a b", [], "isn't a GitHub login"),
        (ME.upper(), [], "author"),
        ("Alice", [ME, "alice"], "already"),
    ],
)
def test_a_bad_choice_is_refused_without_running_anything(
    monkeypatch: pytest.MonkeyPatch, login: str, assignees: list[str], says: str
) -> None:
    def run(args: list[str]) -> str:
        pytest.fail(f"ran {args}")

    monkeypatch.setattr(client, "run", run)
    config, db = stored(
        github=snapshot(mine=[pr(1, assignees=assignees)]), primary_reviewer_repos=[REPO]
    )
    with pytest.raises(ActionError, match=says):
        perform_action(config, db, PRIMARY_REVIEWER, {"pr": URL, "input": login})


def test_only_my_open_prs_in_a_listed_repo_can_be_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(args: list[str]) -> str:
        pytest.fail(f"ran {args}")

    monkeypatch.setattr(client, "run", run)
    config, db = stored(github=snapshot(mine=[pr(1)]), primary_reviewer_repos=[REPO])
    fields = {"pr": "https://github.com/other/repo/pull/9", "input": "alice"}
    with pytest.raises(ActionError, match="isn't one of my open PRs"):
        perform_action(config, db, PRIMARY_REVIEWER, fields)
    config, db = stored(github=snapshot(mine=[pr(1)]))
    with pytest.raises(ActionError, match="primary_reviewer_repos"):
        perform_action(config, db, PRIMARY_REVIEWER, {"pr": URL, "input": "alice"})


def test_github_refusing_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str]) -> str:
        raise client.CommandError("no such user")

    monkeypatch.setattr(client, "run", run)
    config, db = stored(github=snapshot(mine=[pr(1)]), primary_reviewer_repos=[REPO])
    with pytest.raises(ActionError, match="no such user"):
        perform_action(config, db, PRIMARY_REVIEWER, {"pr": URL, "input": "alice"})
