"""github's buttons that change one of my PRs on GitHub."""

import pytest
from conftest import REPO, dashboard, panel, pr, snapshot, stored

from move_forward.plugin import ActionError
from move_forward.plugins.github import client
from move_forward.plugins.github.models import Check
from move_forward.plugins.github.panels import MARK_READY, RERUN_FAILED, UPDATE_BRANCH
from move_forward.refresh import perform_action

URL = f"https://github.com/{REPO}/pull/1"


def _commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """What the action runs, instead of running it."""
    commands: list[list[str]] = []

    def run(args: list[str]) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(client, "run", run)
    return commands


def test_only_a_draft_that_may_be_ready_has_the_button() -> None:
    ready = dashboard(github=snapshot(mine=[pr(1, is_draft=True)]))
    [button] = [a for a in panel(ready, "github.metadata").items[0].actions if a.name == MARK_READY]
    assert button.confirm is not None
    failing = dashboard(github=snapshot(mine=[pr(1, is_draft=True, rollup_state="FAILURE")]))
    assert not [
        a for a in panel(failing, "github.metadata").items[0].actions if a.name == MARK_READY
    ]


def test_marking_ready_tells_github_and_fetches_the_pr_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[pr(1, is_draft=True)]))
    result = perform_action(config, db, MARK_READY, {"pr": URL})
    assert commands == [["gh", "pr", "ready", URL]]
    assert [ref.id for ref in result.touched] == [URL]


def test_only_a_draft_of_mine_can_be_marked_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[pr(1)]))
    with pytest.raises(ActionError, match="isn't a draft"):
        perform_action(config, db, MARK_READY, {"pr": URL})
    with pytest.raises(ActionError, match="isn't one of my open PRs"):
        perform_action(config, db, MARK_READY, {"pr": "https://github.com/o/r/pull/9"})
    assert commands == []


def test_a_pr_held_as_behind_its_base_can_be_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    behind = pr(1, merge_state="BEHIND", review_decision="APPROVED")
    built = dashboard(github=snapshot(mine=[behind]))
    [item] = panel(built, "github.held").items
    [button] = [a for a in item.actions if a.name == UPDATE_BRANCH]
    assert button.confirm is not None
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[behind]))
    result = perform_action(config, db, UPDATE_BRANCH, {"pr": URL})
    assert commands == [["gh", "pr", "update-branch", URL]]
    assert [ref.id for ref in result.touched] == [URL]


def test_a_pr_held_otherwise_has_no_update_button() -> None:
    blocked = pr(1, merge_state="BLOCKED", review_decision="APPROVED")
    [item] = panel(dashboard(github=snapshot(mine=[blocked])), "github.held").items
    assert not [a for a in item.actions if a.name == UPDATE_BRANCH]


def test_only_a_pr_of_mine_thats_behind_can_be_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[pr(1)]))
    with pytest.raises(ActionError, match="isn't behind"):
        perform_action(config, db, UPDATE_BRANCH, {"pr": URL})
    with pytest.raises(ActionError, match="isn't one of my open PRs"):
        perform_action(config, db, UPDATE_BRANCH, {"pr": "https://github.com/o/r/pull/9"})
    assert commands == []


def test_github_refusing_to_update_is_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args: list[str]) -> str:
        raise client.CommandError("merge conflict")

    monkeypatch.setattr(client, "run", run)
    config, db = stored(github=snapshot(mine=[pr(1, merge_state="BEHIND")]))
    with pytest.raises(ActionError, match="merge conflict"):
        perform_action(config, db, UPDATE_BRANCH, {"pr": URL})


def test_a_ci_failure_with_failed_runs_can_run_them_again(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = "https://github.com/acme/widgets/actions/runs"
    failing = pr(
        1,
        checks=[
            Check(name="tests", state="FAILURE", url=f"{runs}/11/job/1"),
            Check(name="lint", state="FAILURE", url=f"{runs}/11/job/2"),  # the same run
            Check(name="build", state="FAILURE", url=f"{runs}/12"),
            Check(name="policy", state="FAILURE", url=f"{runs}/13/job/3"),
            Check(name="other", state="FAILURE", url="https://ci.example/9"),  # no run
        ],
    )
    built = dashboard(github=snapshot(mine=[failing]), policy_checks=["policy"])
    [item] = panel(built, "github.ci_failures").items
    assert [a.label for a in item.actions if a.name == RERUN_FAILED] == ["Rerun failed"]
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[failing]), policy_checks=["policy"])
    result = perform_action(config, db, RERUN_FAILED, {"pr": URL})
    repo = f"github.com/{REPO}"
    assert commands == [
        ["gh", "run", "rerun", run, "--failed", "--repo", repo] for run in ("11", "12")
    ]
    assert [ref.id for ref in result.touched] == [URL]


def test_a_ci_failure_naming_no_run_has_no_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = pr(1, checks=[Check(name="tests", state="FAILURE", url="https://ci.example/9")])
    [item] = panel(dashboard(github=snapshot(mine=[failing])), "github.ci_failures").items
    assert not [a for a in item.actions if a.name == RERUN_FAILED]
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[failing]))
    with pytest.raises(ActionError, match="no failed Actions run"):
        perform_action(config, db, RERUN_FAILED, {"pr": URL})
    assert commands == []
