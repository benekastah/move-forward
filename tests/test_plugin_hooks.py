"""Core's hooks for plugins: buttons on other plugins' items, pages and commands."""

import argparse
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import NOW, dashboard, issue, pr, snapshot, stored
from pydantic import BaseModel

from move_forward import cli, refresh, registry, render
from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import Model
from move_forward.graph import Entity, Graph, Ref
from move_forward.page import Action, Item
from move_forward.plugin import CliHandler, Context, PageHandler
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.repositories import KvRepository


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
    [item] = built.panel("github.conflicts").items
    assert [a.href for a in item.actions if a.href] == [f"/hello?pr={pr(1).url}"]
    html = render.dashboard_page(built, refreshing=False)
    assert f"<a class='button link-action' href='/hello?pr={pr(1).url}'>Look</a>" in html
    assert all(not a.href for a in built.panel("notes.todos").actions)


def test_without_its_section_a_plugin_adds_nothing() -> None:
    built = dashboard(github=snapshot(mine=[pr(1, merge_state="DIRTY")]))
    [item] = built.panel("github.conflicts").items
    assert not any(a.href for a in item.actions)


def test_plugin_pages_are_served_from_the_graph() -> None:
    config = Config.model_validate(
        {"email": "me@x", "github": {"org": "acme"}, "jira": {"site": "https://j"}}
    )
    db = Database(":memory:")
    registry.migrate(config, db)
    PrRepository(db).replace(snapshot(mine=[pr(1, title="Fix <it>")]))
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


class _TicketAttrs(Model):
    summary: str


class _Declaring(JiraPlugin):
    """Jira, declaring its tickets need a summary, and publishing one without."""

    kinds: ClassVar[dict[str, type[BaseModel]]] = {"ticket": _TicketAttrs}

    def entities(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None) -> list[Entity]:
        return [
            Entity(ref=Ref(kind="ticket", id="APP-1"), title="good", attrs={"summary": "s"}),
            Entity(ref=Ref(kind="ticket", id="APP-2"), title="bad", attrs={"summary": 3.5}),
        ]


def test_an_entity_that_doesnt_fit_its_kind_is_left_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    plugins = [_Declaring() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config = Config.model_validate({"email": "me@x", "jira": {"site": "https://j"}})
    db = Database(":memory:")
    registry.migrate(config, db)
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert [t.ref.id for t in graph.of_kind("ticket")] == ["APP-1"]
    assert "published a bad ticket APP-2" in caplog.text


class _VeilAttrs(Model):
    pass


class _Veiling(Hooked):
    """Jira, plus a hiding `veil` over PR 1 and ticket APP-3."""

    kinds: ClassVar[dict[str, type[BaseModel]]] = {**JiraPlugin.kinds, "veil": _VeilAttrs}
    hides: ClassVar[list[str]] = ["veil"]

    def entities(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None) -> list[Entity]:
        veil = Entity(
            ref=Ref(kind="veil", id="1"),
            title="veil",
            refs=[Ref(kind="pr", id=pr(1).url), Ref(kind="ticket", id="APP-3")],
        )
        return [*super().entities(ctx, snapshot), veil]


def test_core_leaves_out_items_about_what_a_hiding_kind_covers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins = [_Veiling() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    mine = [pr(n, merge_state="DIRTY") for n in (1, 2, 3)]
    built = dashboard(github=snapshot(mine=mine), jira_site="https://j")
    # PR 1 directly; PR 3 through APP-3, which its body mentions.
    assert [i.title for i in built.panel("github.conflicts").items] == ["PR 2"]
    assert (
        dashboard(github=snapshot(mine=mine)).panel("github.conflicts").items
    )  # no veil, no hiding


class _Unruly(JiraPlugin):
    """Jira, publishing a ticket twice, one of github's PRs, and a kind it never declared."""

    def entities(self, ctx: Context[JiraConfig], snapshot: JiraSnapshot | None) -> list[Entity]:
        [ticket] = super().entities(ctx, snapshot)
        return [
            ticket,
            ticket.model_copy(update={"title": "again"}),
            Entity(ref=Ref(kind="pr", id=pr(1).url), title="not mine to publish"),
            Entity(ref=Ref(kind="thing", id="1"), title="undeclared"),
        ]


class _Claiming(JiraPlugin):
    """Jira, declaring github's `pr` too."""

    kinds: ClassVar[dict[str, type[BaseModel]]] = {**JiraPlugin.kinds, "pr": _VeilAttrs}


def _graph_with(plugin: JiraPlugin, monkeypatch: pytest.MonkeyPatch) -> Graph:
    plugins = [plugin if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    return refresh.stored_graph(db, registry.enabled(config, db, NOW))[0]


def test_a_plugin_publishes_only_the_kinds_it_owns_and_each_ref_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    graph = _graph_with(_Unruly(), monkeypatch)
    assert [e.title for e in graph.of_kind("ticket")] == ["APP-1 Do the thing"]
    assert [e.title for e in graph.of_kind("pr")] == [pr(1).title]  # github's own
    assert graph.of_kind("thing") == []
    assert "jira published ticket APP-1 twice" in caplog.text
    assert f"jira published pr {pr(1).url}, a kind it doesn't own" in caplog.text
    assert "jira published thing 1, a kind it doesn't own" in caplog.text


def test_a_kind_two_plugins_declare_is_the_first_ones(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    graph = _graph_with(_Claiming(), monkeypatch)
    assert [e.title for e in graph.of_kind("pr")] == [pr(1).title]
    assert "github and jira both declare pr; jira's are left out" in caplog.text


class _Narrowed(JiraPlugin):
    """Jira, finding only OPS keys in text."""

    def linking_patterns(self, ctx: Context[JiraConfig]) -> dict[str, str | None]:
        return {"ticket": r"\bOPS-\d+\b"}


def test_a_plugin_can_narrow_its_linking_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _graph_with(_Narrowed(), monkeypatch)
    assert graph.named("APP-1 and OPS-2") == [Ref(kind="ticket", id="OPS-2")]


class _Prepared(JiraPlugin):
    """Jira, adding a Look link to PR items from work it does once per page."""

    prepared = 0

    def prepare_item_actions(
        self, ctx: Context[JiraConfig], graph: Graph
    ) -> Callable[[Item], list[Action]]:
        _Prepared.prepared += 1
        prs = {e.ref.id for e in graph.of_kind("pr")}

        def actions(item: Item) -> list[Action]:
            pr = item.subject("pr")
            if pr is None or pr.id not in prs:
                return []
            return [Action(name="", label="Look", fields={}, href=f"/look?pr={pr.id}")]

        return actions


def test_a_plugin_prepares_its_item_actions_once_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [_Prepared() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    mine = [pr(1, merge_state="DIRTY"), pr(2, merge_state="DIRTY")]
    built = dashboard(github=snapshot(mine=mine), jira_site="https://j")
    assert _Prepared.prepared == 1
    looks = [a.href for i in built.panel("github.conflicts").items for a in i.actions if a.href]
    assert looks == [f"/look?pr={pr(1).url}", f"/look?pr={pr(2).url}"]


def test_today_is_the_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        late_evening = datetime(2026, 10, 31, 3, 0, tzinfo=UTC)  # 8pm on the 30th there
        ctx = Context(
            config=JiraConfig(site="https://j"),
            email="me@x",
            teams=[],
            prune_days=90,
            now=late_evening,
            db=Database(":memory:"),
            kv=KvRepository(Database(":memory:"), "jira"),
        )
        assert ctx.today == date(2026, 10, 30)
    finally:
        monkeypatch.undo()
        time.tzset()
