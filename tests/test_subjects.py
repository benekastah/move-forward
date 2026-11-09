"""Fetching some entities again, through the plugins that publish them, instead of
refreshing the plugins whole."""

import time
from typing import ClassVar

import pytest
from conftest import NOW, issue, pr, snapshot, stored

from move_forward import refresh, registry
from move_forward.graph import Ref
from move_forward.plugin import Subjects, SubjectsRequest
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.jira.models import JiraSnapshot

PR_1 = Ref(kind="pull_request", id=pr(1).url)
APP_1 = Ref(kind="ticket", id="https://jira/browse/APP-1")


class _Recording(GitHubPlugin):
    """github, recording what it's asked to fetch again and keeping its snapshot."""

    asked: ClassVar[list[list[Ref]]] = []
    slow: ClassVar[bool] = False

    def refresh_subjects(
        self, req: SubjectsRequest[GitHubConfig, GitHubSnapshot]
    ) -> Subjects[GitHubSnapshot] | None:
        self.asked.append(req.refs)
        if self.slow:
            time.sleep(0.5)
        assert req.snapshot is not None
        return Subjects(req.snapshot, req.refs)


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> None:
    swap = {"github": _Recording()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setattr(_Recording, "asked", [])


@pytest.mark.usefixtures("recording")
def test_each_plugin_is_asked_for_its_own_and_one_that_cant_is_refreshed_whole() -> None:
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    unknown = Ref(kind="pull_request", id="https://github.com/acme/widgets/pull/99")
    fallback = refresh.refresh_subjects(config, db, [PR_1, APP_1, unknown], NOW)
    assert _Recording.asked == [[PR_1]]  # not the ticket, nor a PR the graph doesn't have
    assert fallback == {"jira"}  # jira can't fetch one ticket: it's refreshed whole


@pytest.mark.usefixtures("recording")
def test_a_plugin_taking_too_long_is_refreshed_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_Recording, "slow", True)
    monkeypatch.setattr(_Recording, "subjects_timeout_seconds", 0.05)
    config, db = stored(github=snapshot(mine=[pr(1)]))
    assert refresh.refresh_subjects(config, db, [PR_1], NOW) == {"github"}


@pytest.mark.usefixtures("recording")
def test_many_entities_are_fetched_fifty_at_a_time() -> None:
    prs = [pr(n) for n in range(1, 56)]
    config, db = stored(github=snapshot(mine=prs))
    refs = [Ref(kind="pull_request", id=p.url) for p in prs]
    assert refresh.refresh_subjects(config, db, refs, NOW) == set()
    assert [len(asked) for asked in _Recording.asked] == [50, 5]
