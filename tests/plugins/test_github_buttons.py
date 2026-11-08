"""github's buttons that change one of my PRs on GitHub."""

import pytest
from conftest import REPO, dashboard, entry, panel, pr, snapshot, stored

from move_forward.api.shell import CommandError
from move_forward.page import Badge
from move_forward.plugin import ActionError
from move_forward.plugins.github import client
from move_forward.plugins.github.models import Check, PullRequest, Thread
from move_forward.plugins.github.panels import (
    AUTO_MERGE,
    CANCEL_AUTO_MERGE,
    MARK_READY,
    MERGE,
    RERUN_FAILED,
    UPDATE_BRANCH,
)
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
    with pytest.raises(ActionError, match="no failed Actions run that's over"):
        perform_action(config, db, RERUN_FAILED, {"pr": URL})
    assert commands == []


def test_rerun_leaves_out_a_run_still_going_or_another_repos_and_tries_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = "https://github.com/acme/widgets/actions/runs"
    failing = pr(
        1,
        checks=[
            Check(name="a", state="FAILURE", url=f"{runs}/21/job/1"),
            Check(name="b", state="PENDING", url=f"{runs}/21/job/2"),  # its run isn't over
            Check(name="c", state="FAILURE", url="https://github.com/acme/ci/actions/runs/22"),
            Check(name="d", state="FAILURE", url=f"{runs}/23?pr=1"),
            Check(name="e", state="FAILURE", url=f"{runs}/24#step"),
        ],
    )
    commands: list[list[str]] = []

    def run(args: list[str]) -> str:
        commands.append(args)
        if args[3] == "23":
            raise CommandError("already running")
        return ""

    monkeypatch.setattr(client, "run", run)
    config, db = stored(github=snapshot(mine=[failing]))
    result = perform_action(config, db, RERUN_FAILED, {"pr": URL})
    assert [c[3] for c in commands] == ["23", "24"]  # each tried, though 23 was refused
    assert "(1 run(s))" in result.notice and "run 23: already running" in result.notice


def _ready(**overrides: object) -> PullRequest:
    return pr(1, review_decision="APPROVED", head_oid="abc1234def", **overrides)


def test_a_pr_ready_to_merge_merges_with_the_method_i_like(monkeypatch: pytest.MonkeyPatch) -> None:
    ready = _ready(merge_methods=["MERGE", "SQUASH"], default_merge_method="MERGE")
    built = dashboard(github=snapshot(mine=[ready]), merge={"method": "squash"})
    [item] = panel(built, "github.ready_to_merge").items
    [button] = [a for a in item.actions if a.name == MERGE]
    assert button.label == "Merge" and button.confirm is not None and "(squash)" in button.confirm
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[ready]), merge={"method": "squash"})
    result = perform_action(config, db, MERGE, button.fields)
    assert commands == [
        [
            *["gh", "api", "--hostname", "github.com", "-X", "PUT"],
            f"repos/{REPO}/pulls/1/merge-async",
        ]
        + [
            arg
            for f in ("merge_method=squash", "sha=abc1234def", "merge_action=direct_merge")
            for arg in ("-f", f)
        ]
    ]
    assert "merging (squash)" in result.notice and [r.id for r in result.touched] == [URL]


def test_with_a_merge_queue_it_adds_to_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    queued = _ready(merge_queue=True, merge_methods=["SQUASH"])
    [item] = panel(dashboard(github=snapshot(mine=[queued])), "github.ready_to_merge").items
    [button] = [a for a in item.actions if a.name == MERGE]
    assert button.label == "Add to queue"
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[queued]))
    perform_action(config, db, MERGE, button.fields)
    assert commands == [["gh", "pr", "merge", URL, "--match-head-commit", "abc1234def"]]


def test_no_merge_while_how_the_repo_merges_isnt_known_or_the_pr_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown = _ready()  # no merge methods fetched yet
    [item] = panel(dashboard(github=snapshot(mine=[unknown])), "github.ready_to_merge").items
    assert not [a for a in item.actions if a.name == MERGE]
    commands = _commands(monkeypatch)
    known = _ready(merge_methods=["SQUASH"])
    config, db = stored(github=snapshot(mine=[known]))
    with pytest.raises(ActionError, match="new commits"):
        perform_action(config, db, MERGE, {"pr": URL, "head": "0000000"})
    unapproved = pr(1, head_oid="abc1234def", merge_methods=["SQUASH"])
    config, db = stored(github=snapshot(mine=[unapproved]))
    with pytest.raises(ActionError, match="isn't ready"):
        perform_action(config, db, MERGE, {"pr": URL, "head": "abc1234def"})
    assert commands == []


def test_merge_refuses_what_ready_to_merge_holds_and_takes_what_it_offers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held = _ready(merge_methods=["SQUASH"])
    noted = entry("APP-1 Fix", [], **{"Merge conditions": ["after the migration"], "Where": [URL]})
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[held]), work_items=[noted])
    with pytest.raises(ActionError, match="after the migration"):
        perform_action(config, db, MERGE, {"pr": URL, "head": "abc1234def"})
    open_thread = Thread(resolved=False, outdated=False, url="u")
    threads = _ready(merge_methods=["SQUASH"], threads=[open_thread])
    config, db = stored(github=snapshot(mine=[threads]))
    with pytest.raises(ActionError, match="isn't ready"):
        perform_action(config, db, MERGE, {"pr": URL, "head": "abc1234def"})
    assert commands == []
    # A base that needs no review and has no checks: Ready to merge offers it, so Merge takes it.
    unreviewed = pr(
        1,
        head_oid="abc1234def",
        merge_methods=["SQUASH"],
        review_decision=None,
        rollup_state=None,
        checks=[],
    )
    built = dashboard(github=snapshot(mine=[unreviewed]))
    [item] = panel(built, "github.ready_to_merge").items
    [button] = [a for a in item.actions if a.name == MERGE]
    config, db = stored(github=snapshot(mine=[unreviewed]))
    perform_action(config, db, MERGE, button.fields)
    assert commands and commands[-1][-1] == "merge_action=direct_merge"


def _awaiting(**overrides: object) -> PullRequest:
    return pr(1, head_oid="abc1234def", requested_users=["alice"], **overrides)


def test_a_pr_awaiting_review_can_merge_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    waiting = _awaiting(auto_merge_allowed=True, merge_methods=["SQUASH"])
    [item] = panel(dashboard(github=snapshot(mine=[waiting])), "github.ready_for_review").items
    [button] = [a for a in item.actions if a.name == AUTO_MERGE]
    assert button.confirm is not None and "(squash)" in button.confirm
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[waiting]))
    perform_action(config, db, AUTO_MERGE, button.fields)
    queued = _awaiting(auto_merge_allowed=True, merge_queue=True)
    config, db = stored(github=snapshot(mine=[queued]))
    perform_action(config, db, AUTO_MERGE, {"pr": URL, "head": "abc1234def"})
    assert commands == [
        ["gh", "pr", "merge", URL, "--auto", "--squash", "--match-head-commit", "abc1234def"],
        ["gh", "pr", "merge", URL, "--auto", "--match-head-commit", "abc1234def"],  # queue picks
    ]


def test_auto_merge_on_is_badged_and_can_be_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    on = _awaiting(auto_merge_allowed=True, merge_methods=["SQUASH"], auto_merge=True)
    [item] = panel(dashboard(github=snapshot(mine=[on])), "github.ready_for_review").items
    assert Badge(text="merges when ready", tone="good") in item.badges
    assert not [a for a in item.actions if a.name == AUTO_MERGE]
    [cancel] = [a for a in item.actions if a.name == CANCEL_AUTO_MERGE]
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[on]))
    perform_action(config, db, CANCEL_AUTO_MERGE, cancel.fields)
    assert commands == [["gh", "pr", "merge", URL, "--disable-auto"]]


def test_no_merge_when_ready_where_not_allowed_or_held(monkeypatch: pytest.MonkeyPatch) -> None:
    not_allowed = _awaiting(merge_methods=["SQUASH"])
    [item] = panel(dashboard(github=snapshot(mine=[not_allowed])), "github.ready_for_review").items
    assert not [a for a in item.actions if a.name == AUTO_MERGE]
    held = _awaiting(auto_merge_allowed=True, merge_methods=["SQUASH"])
    noted = entry("APP-1 Fix", [], **{"Merge conditions": ["after the migration"], "Where": [URL]})
    built = dashboard(github=snapshot(mine=[held]), work_items=[noted])
    [item] = panel(built, "github.ready_for_review").items
    assert not [a for a in item.actions if a.name == AUTO_MERGE]
    commands = _commands(monkeypatch)
    config, db = stored(github=snapshot(mine=[held]), work_items=[noted])
    with pytest.raises(ActionError, match="after the migration"):
        perform_action(config, db, AUTO_MERGE, {"pr": URL, "head": "abc1234def"})
    assert commands == []


def test_merge_when_ready_is_refused_where_it_would_merge_at_once_or_cant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _commands(monkeypatch)
    no_review = _awaiting(auto_merge_allowed=True, merge_methods=["SQUASH"], review_decision=None)
    [item] = panel(dashboard(github=snapshot(mine=[no_review])), "github.ready_for_review").items
    assert not [a for a in item.actions if a.name == AUTO_MERGE]  # it would merge at once
    cases = [
        (no_review, "abc1234def", "would merge at once"),
        (_awaiting(auto_merge_allowed=True, merge_methods=["SQUASH"]), "0000000", "new commits"),
        (_awaiting(merge_methods=["SQUASH"]), "abc1234def", "doesn't allow auto-merge"),
        (_awaiting(auto_merge_allowed=True), "abc1234def", "isn't known yet"),
    ]
    for case, head, said in cases:
        config, db = stored(github=snapshot(mine=[case]))
        with pytest.raises(ActionError, match=said):
            perform_action(config, db, AUTO_MERGE, {"pr": URL, "head": head})
    assert commands == []


def test_auto_merge_on_shows_wherever_the_pr_is_and_warns_when_held() -> None:
    failing = Check(name="tests", state="FAILURE", url="https://ci/1")
    on = pr(1, auto_merge=True, auto_merge_allowed=True, checks=[failing], rollup_state="FAILURE")
    built = dashboard(github=snapshot(mine=[on]))
    [fixing] = panel(built, "github.fix_before_review").items
    assert Badge(text="merges when ready", tone="good") in fixing.badges
    assert [a.name for a in fixing.actions if a.name == CANCEL_AUTO_MERGE] == [CANCEL_AUTO_MERGE]
    noted = entry("APP-1 Fix", [], **{"Merge conditions": ["after the migration"], "Where": [URL]})
    held = dashboard(github=snapshot(mine=[on]), work_items=[noted])
    [fixing] = panel(held, "github.fix_before_review").items
    assert Badge(text="merges when ready, though held", tone="warn") in fixing.badges
