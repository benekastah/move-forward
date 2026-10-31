"""Core's hooks for plugins: buttons on other plugins' items, and pages."""

import pytest
from conftest import NOW, dashboard, pr, snapshot

from move_forward import refresh, registry, render
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.page import Action, Item
from move_forward.plugin import Context, PageHandler
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import SnapshotRepository


class Hooked(JiraPlugin):
    """Jira, plus a link on every PR item and a page at /hello."""

    def item_actions(self, ctx: Context[JiraConfig], item: Item, graph: Graph) -> list[Action]:
        if item.subject is None or item.subject.kind != "pr":
            return []
        return [Action(name="", label="Look", fields={}, href=f"/hello?pr={item.subject.id}")]

    def pages(self) -> dict[str, PageHandler]:
        def hello(
            ctx: Context[JiraConfig], graph: Graph, query: dict[str, list[str]]
        ) -> str | None:
            pr_url = query.get("pr", [""])[0]
            entity = graph.get("pr", pr_url)
            return render.page("Hello", "hello", render.e(entity.title)) if entity else None

        return {"/hello": hello}


@pytest.fixture(autouse=True)
def hooked(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [Hooked() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)


def test_a_plugin_adds_buttons_to_another_plugins_items() -> None:
    built = dashboard(github=snapshot(mine=[pr(1, merge_state="DIRTY")]), jira_site="https://j")
    [item] = built.panel("conflicts").items
    assert [a.href for a in item.actions if a.href] == [f"/hello?pr={pr(1).url}"]
    html = render.dashboard_page(built, refreshing=False)
    assert f"<a class='button link-action' href='/hello?pr={pr(1).url}'>Look</a>" in html
    assert all(not a.href for a in built.panel("todos").actions)


def test_without_its_section_a_plugin_adds_nothing() -> None:
    built = dashboard(github=snapshot(mine=[pr(1, merge_state="DIRTY")]))
    [item] = built.panel("conflicts").items
    assert not any(a.href for a in item.actions)


def test_plugin_pages_are_served_from_the_graph() -> None:
    config = Config.model_validate(
        {"email": "me@x", "github": {"org": "acme"}, "jira": {"site": "https://j"}}
    )
    db = Database(":memory:")
    SnapshotRepository(db).save("github", NOW, snapshot(mine=[pr(1, title="Fix <it>")]))
    page = refresh.plugin_page(config, db, "/hello", {"pr": [pr(1).url]})
    assert page is not None and "Fix &lt;it&gt;" in page and "/ hello</h1>" in page
    assert refresh.plugin_page(config, db, "/hello", {"pr": ["nope"]}) is None
    assert refresh.plugin_page(config, db, "/nowhere", {}) is None
