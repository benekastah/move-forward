"""github's buttons that change one of my PRs on GitHub."""

import pytest
from conftest import REPO, dashboard, panel, pr, snapshot, stored

from move_forward.plugin import ActionError
from move_forward.plugins.github import client
from move_forward.plugins.github.panels import MARK_READY
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
    assert _commands(monkeypatch) == []
    config, db = stored(github=snapshot(mine=[pr(1)]))
    with pytest.raises(ActionError, match="isn't a draft"):
        perform_action(config, db, MARK_READY, {"pr": URL})
    with pytest.raises(ActionError, match="isn't one of my open PRs"):
        perform_action(config, db, MARK_READY, {"pr": "https://github.com/o/r/pull/9"})
