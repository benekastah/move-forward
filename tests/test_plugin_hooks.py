"""Core's hooks for plugins: buttons on other plugins' items, pages and commands."""

import argparse
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, dashboard, pr, snapshot

from move_forward import cli, refresh, registry, render
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref
from move_forward.page import Action, Item
from move_forward.plugin import CliHandler, Context, PageHandler
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import SnapshotRepository


class Hooked(JiraPlugin):
    """Jira, plus a link on every PR item and a page at /hello."""

    def item_actions(self, ctx: Context[JiraConfig], item: Item, graph: Graph) -> list[Action]:
        pr = item.subject("pr")
        if pr is None:
            return []
        return [Action(name="", label="Look", fields={}, href=f"/hello?pr={pr.id}")]

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
    registry.migrate(config, db)
    SnapshotRepository(db).save("github", NOW, snapshot(mine=[pr(1, title="Fix <it>")]))
    page = refresh.plugin_page(config, db, "/hello", {"pr": [pr(1).url]})
    assert page is not None and "Fix &lt;it&gt;" in page and "/ hello</h1>" in page
    assert refresh.plugin_page(config, db, "/hello", {"pr": ["nope"]}) is None
    assert refresh.plugin_page(config, db, "/nowhere", {}) is None


class _Counted(JiraPlugin):
    """Jira, plus `mfd jira count`, which prints how many tickets the graph has."""

    cli_help: ClassVar[str | None] = "count things"

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        parser.add_argument("what")

        def run(ctx: Context[JiraConfig], graph: Graph, args: argparse.Namespace) -> int:
            print(f"{args.what}: {len(graph.of_kind('ticket'))} for {ctx.email}")
            return 3

        return run


def test_a_plugin_adds_an_mfd_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plugins = [_Counted() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config = tmp_path / "config.toml"
    db = tmp_path / "dashboard.db"
    config.write_text(f'email = "me@x"\ndb_path = "{db}"\n')
    assert cli.main(["-c", str(config), "jira", "tickets"]) == 1
    assert "[jira] isn't in" in capsys.readouterr().err
    config.write_text(config.read_text() + '\n[jira]\nsite = "https://j"\n')
    assert cli.main(["-c", str(config), "jira", "tickets"]) == 3
    assert capsys.readouterr().out == "tickets: 0 for me@x\n"


class _Fetching(JiraPlugin):
    """Jira, fetching any ticket asked for (and, wrongly, a PR along with it)."""

    asked: ClassVar[list[list[str]]] = []

    def fetch(self, ctx: Context[JiraConfig], kind: str, ids: list[str]) -> list[Entity]:
        self.asked.append(ids)
        if kind != "ticket":
            return []
        return [Entity(ref=Ref(kind="ticket", id=i), title=i) for i in ids] + [
            Entity(ref=Ref(kind="pr", id="https://pr/1"), title="stray")
        ]


def test_the_graph_fetches_through_the_plugin_that_owns_the_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins = [_Fetching() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config = Config.model_validate({"email": "me@x", "jira": {"site": "https://j"}})
    db = Database(":memory:")
    registry.migrate(config, db)
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert list(graph.fetch("ticket", ["APP-1", "APP-2"])) == ["APP-1", "APP-2"]
    assert graph.fetch("ticket", []) == {}
    assert graph.fetch("pr", ["https://pr/1"]) == {}
    assert _Fetching.asked == [["APP-1", "APP-2"], ["https://pr/1"]]


class _Failing(JiraPlugin):
    def fetch(self, ctx: Context[JiraConfig], kind: str, ids: list[str]) -> list[Entity]:
        raise RuntimeError("no Jira token")


def test_a_plugin_failing_to_fetch_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [_Failing() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config = Config.model_validate({"email": "me@x", "jira": {"site": "https://j"}})
    db = Database(":memory:")
    registry.migrate(config, db)
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.fetch("ticket", ["APP-1"]) == {}
