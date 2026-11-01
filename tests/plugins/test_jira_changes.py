"""jira telling that a ticket of mine changed, with a one-result search."""

from datetime import timedelta

import pytest
from conftest import NOW

from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.plugin import Context
from move_forward.plugins.jira import client
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import KvRepository


def test_the_plugin_says_changed_when_a_ticket_of_mine_was_updated_since_it_looked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[dict[str, object]] = []
    answers = ['{"issues": [{"key": "APP-1"}]}', '{"issues": []}']

    def search_page(
        config: JiraConfig, email: str, api_token: str, body: dict[str, object]
    ) -> bytes:
        asked.append(body)
        return answers.pop(0).encode()

    monkeypatch.setattr(client, "_search_page", search_page)
    monkeypatch.setenv("JIRA_API_TOKEN", "t")
    db = Database(":memory:")
    plugin = JiraPlugin()

    def changed(seconds: int) -> bool:
        ctx = Context(
            config=JiraConfig(site="https://j.example"),
            email="me@x",
            teams=[],
            prune_days=90,
            now=NOW + timedelta(seconds=seconds),
            db=db,
            kv=KvRepository(db, "jira"),
        )
        return plugin.changed(ctx, Graph([]))

    assert not changed(0) and asked == []  # the first look only notes the time
    assert changed(90)
    assert asked[-1]["jql"] == "assignee = currentUser() AND updated >= -3m"  # 1.5 → 2, + 1
    assert asked[-1]["maxResults"] == 1
    assert not changed(150)
    assert asked[-1]["jql"] == "assignee = currentUser() AND updated >= -2m"
