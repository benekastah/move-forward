"""Core's hooks for plugins: buttons on other plugins' items, pages and commands."""

import argparse
import inspect
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, get_origin

import pytest
from conftest import ME, NOW, dashboard, issue, local_snapshot, pr, snapshot, stored
from pydantic import BaseModel

from move_forward import api, cli, localtime, refresh, registry, render
from move_forward.act_now import ActNowConfig
from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import KindModel
from move_forward.graph import Entity, Graph, KindSpec, Ref
from move_forward.page import Action, Identity, Item, ItemAdditions
from move_forward.plugin import (
    ActNowRequest,
    CliHandler,
    CliRequest,
    Context,
    ContextRequest,
    FetchRequest,
    GraphRequest,
    ItemAdditionsFor,
    ItemRequest,
    PageHandler,
    PageRequest,
    Plugin,
    RefRequest,
    SnapshotRequest,
)
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.models import GitHubSnapshot
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.jira.config import JiraConfig
from move_forward.plugins.jira.models import JiraSnapshot
from move_forward.plugins.jira.plugin import JiraPlugin
from move_forward.refresh import build_dashboard
from move_forward.repositories import KvRepository


class Hooked(JiraPlugin):
    """Jira, plus a link on every PR item and a page at /jira/hello."""

    def item_actions(self, req: GraphRequest[JiraConfig]) -> ItemAdditionsFor:
        def additions(each: ItemRequest) -> ItemAdditions:
            pr = each.item.subject("pull_request")
            if pr is None:
                return ItemAdditions()
            look = Action(name="", label="Look", fields={}, href=f"/jira/hello?pr={pr.id}")
            seen = f"seen by {self.name} on {each.surface}, {each.owner}'s"
            return ItemAdditions(actions=[look], badges=[seen])

        return additions

    def pages(self) -> dict[str, PageHandler]:
        def hello(req: PageRequest[JiraConfig]) -> str | None:
            graph, query = req.graph, req.query
            pr_url = query.get("pr", [""])[0]
            entity = graph.get("pull_request", pr_url)
            return render.page("Hello", "hello", render.e(entity.title)) if entity else None

        return {"hello": hello}


@pytest.fixture(autouse=True)
def hooked(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [Hooked() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)


def test_every_hook_is_asked_with_a_shape_or_a_request_extending_one() -> None:
    shapes = (ContextRequest, GraphRequest, RefRequest, SnapshotRequest)
    hooks = {
        name: get_origin(hint) or hint
        for name, hook in inspect.getmembers(Plugin, inspect.isfunction)
        if (hint := inspect.get_annotations(hook).get("req")) is not None
    }
    assert {"panels", "item_actions", "url_for", "load", "store"} <= hooks.keys()
    offered = [getattr(api, name) for name in api.__all__ if name.endswith("Request")]
    for request in [*hooks.values(), *offered]:
        if request is ItemRequest:
            continue  # what item_actions' function is asked per item, not a hook's request
        assert any(issubclass(request, shape) for shape in shapes), request


def test_act_now_tells_a_plugin_the_due_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[timedelta] = []

    def act_now(self: GitHubPlugin, req: ActNowRequest[GitHubConfig, GitHubSnapshot]) -> list[Item]:
        told.append(req.due_horizon)
        return []

    monkeypatch.setattr(GitHubPlugin, "act_now", act_now)
    config, db = stored(github=snapshot())
    ranking = ActNowConfig(due_warning_days=14)
    build_dashboard(config.model_copy(update={"act_now": ranking}), db, NOW)
    assert told == [timedelta(days=14)]


def test_a_plugin_adds_buttons_to_another_plugins_items() -> None:
    built = dashboard(github=snapshot(mine=[pr(1, merge_state="DIRTY")]), jira_site="https://j")
    [item] = built.panel("github.conflicts").items
    assert [a.href for a in item.actions if a.href] == [f"/jira/hello?pr={pr(1).url}"]
    html = render.dashboard_page(built, refreshing=frozenset())
    assert f"<a class='button link-action' href='/jira/hello?pr={pr(1).url}'>Look</a>" in html
    assert all(not a.href for a in built.panel("notes.todos").actions)


def test_what_a_plugin_adds_says_whose_item_and_where_it_is_shown() -> None:
    config, db = stored(github=snapshot(mine=[pr(1, merge_state="DIRTY")]), jira_site="https://j")
    built = build_dashboard(config, db, NOW)
    [item] = built.panel("github.conflicts").items
    assert "seen by jira on dashboard, github's" in item.badges
    html = render.dashboard_page(built, refreshing=frozenset())
    assert "seen by jira on dashboard, github&#x27;s" in html  # a badge, as the page shows it
    focus = build_dashboard(config, db, NOW, surface="focus")
    [item] = focus.panel("github.conflicts").items
    assert "seen by jira on focus, github's" in item.badges


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
    page = refresh.plugin_page(config, db, "/jira/hello", {"pr": [pr(1).url]})
    assert isinstance(page, str) and "Fix &lt;it&gt;" in page and "/ hello</h1>" in page
    assert refresh.plugin_page(config, db, "/jira/hello", {"pr": ["nope"]}) is None
    assert refresh.plugin_page(config, db, "/nowhere", {}) is None


class _Counted(JiraPlugin):
    """Jira, plus `mfd jira count`, which prints how many tickets the graph has."""

    cli_help: ClassVar[str | None] = "count things"

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        parser.add_argument("what")

        def run(req: CliRequest[JiraConfig]) -> int:
            ctx, graph, args = req.ctx, req.graph, req.args
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
    config.write_text(f'email = "me@x"\ndb_path = "{db.as_posix()}"\n')
    assert cli.main(["-c", str(config), "jira", "tickets"]) == 1
    assert "[jira] isn't in" in capsys.readouterr().err
    config.write_text(config.read_text() + '\n[jira]\nsite = "https://j"\n')
    assert cli.main(["-c", str(config), "jira", "tickets"]) == 3
    assert capsys.readouterr().out == "tickets: 0 for me@x\n"


class _Fetching(JiraPlugin):
    """Jira, fetching any ticket asked for (and, wrongly, a PR along with it, and APP-3
    without saying whether it's done)."""

    asked: ClassVar[list[list[str]]] = []

    def fetch(self, req: FetchRequest[JiraConfig]) -> list[Entity]:
        kind, ids = req.kind, req.ids
        self.asked.append(ids)
        if kind != "ticket":
            return []
        found = [
            Entity(ref=Ref(kind="ticket", id=i), title=i, lifecycle="open")
            if i != "APP-3"
            else Entity(ref=Ref(kind="ticket", id=i), title=i)
            for i in ids
        ]
        return [e.model_copy(update={"attrs": {"resolved_at": None}}) for e in found] + [
            Entity(ref=Ref(kind="pull_request", id="https://pr/1"), title="stray")
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
    assert list(graph.fetch("ticket", ["APP-1", "APP-2", "APP-3"])) == ["APP-1", "APP-2"]
    assert graph.fetch("ticket", []) == {}
    assert graph.fetch("pull_request", ["https://pr/1"]) == {}
    assert _Fetching.asked == [["APP-1", "APP-2", "APP-3"]]  # not about PRs: not its kind


class _Asked(JiraPlugin):
    """Jira, noting each kind core asks it about, by hook."""

    asked: ClassVar[list[tuple[str, str]]] = []

    def fetch(self, req: FetchRequest[JiraConfig]) -> list[Entity]:
        self.asked.append(("fetch", req.kind))
        return []

    def url_for(self, req: RefRequest[JiraConfig]) -> str | None:
        self.asked.append(("url_for", req.kind))
        return super().url_for(req)

    def handle_for(self, req: RefRequest[JiraConfig]) -> str | None:
        self.asked.append(("handle_for", req.kind))
        return super().handle_for(req)


def test_a_plugin_is_asked_only_about_its_own_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [_Asked() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config, db = stored(github=snapshot(), jira_site="https://j")
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    _Asked.asked.clear()
    for kind, id in [("pull_request", "https://github.com/o/r/pull/9"), ("git.branch", "b")]:
        graph.url_for(kind, id)
        graph.handle(kind, id)
        graph.fetch(kind, [id])
    assert _Asked.asked == []
    graph.url_for("ticket", "APP-9")
    graph.handle("ticket", "https://j/browse/APP-9")
    graph.fetch("ticket", ["https://j/browse/APP-9"])
    assert _Asked.asked == [("url_for", "ticket"), ("handle_for", "ticket"), ("fetch", "ticket")]


def test_url_for_is_by_default_the_id_where_the_familys_ids_are_urls() -> None:
    config, db = stored(github=snapshot())
    [github] = [e for e in registry.enabled(config, db, NOW) if e.name == "github"]
    default = Plugin[GitHubConfig, GitHubSnapshot]()
    url = "https://github.com/o/r/pull/9"

    def url_for(kind: str, id: str) -> str | None:
        return default.url_for(RefRequest(github.ctx, kind, id))

    assert url_for("pull_request", url) == url
    assert url_for("pull_request", "#9") is None  # not a URL
    assert url_for("person", "github.com/alice") is None  # a family whose ids aren't URLs
    assert url_for("git.branch", "https://x/y") is None  # not a family's


class _Failing(JiraPlugin):
    def fetch(self, req: FetchRequest[JiraConfig]) -> list[Entity]:
        raise RuntimeError("no Jira token")


def test_a_plugin_failing_to_fetch_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [_Failing() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config = Config.model_validate({"email": "me@x", "jira": {"site": "https://j"}})
    db = Database(":memory:")
    registry.migrate(config, db)
    graph, _ = refresh.stored_graph(db, registry.enabled(config, db, NOW))
    assert graph.fetch("ticket", ["APP-1"]) == {}


class _TicketAttrs(KindModel):
    summary: str


class _Declaring(JiraPlugin):
    """Jira, declaring its tickets need a summary, and publishing one without."""

    kinds: ClassVar[dict[str, KindSpec]] = {"ticket": KindSpec(_TicketAttrs)}

    def entities(self, req: SnapshotRequest[JiraConfig, JiraSnapshot | None]) -> list[Entity]:
        return [
            Entity(ref=Ref(kind="ticket", id="https://j/1"), title="good", attrs={"summary": "s"}),
            Entity(ref=Ref(kind="ticket", id="https://j/2"), title="bad", attrs={"summary": 3.5}),
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
    assert [t.ref.id for t in graph.of_kind("ticket")] == ["https://j/1"]
    assert "published a bad ticket https://j/2" in caplog.text


class _VeilAttrs(KindModel):
    pass


class _Veiling(Hooked):
    """Jira, plus a hiding `veil` over PR 1 and ticket APP-3."""

    kinds: ClassVar[dict[str, KindSpec]] = {
        **JiraPlugin.kinds,
        "jira.veil": KindSpec(_VeilAttrs, hides=True),
    }

    def entities(self, req: SnapshotRequest[JiraConfig, JiraSnapshot | None]) -> list[Entity]:
        veil = Entity(
            ref=Ref(kind="jira.veil", id="1"),
            title="veil",
            refs=[
                Ref(kind="pull_request", id=pr(1).url),
                Ref(kind="ticket", id="https://j/browse/APP-3"),
            ],
        )
        return [*super().entities(req), veil]


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

    def entities(self, req: SnapshotRequest[JiraConfig, JiraSnapshot | None]) -> list[Entity]:
        [ticket] = super().entities(req)
        return [
            ticket,
            ticket.model_copy(update={"title": "again"}),
            Entity(ref=Ref(kind="pull_request", id=pr(1).url), title="not mine to publish"),
            Entity(ref=Ref(kind="thing", id="1"), title="undeclared"),
        ]


def _graph_with(plugin: JiraPlugin, monkeypatch: pytest.MonkeyPatch) -> Graph:
    plugins = [plugin if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    config, db = stored(github=snapshot(mine=[pr(1)]), jira=JiraSnapshot(issues=[issue("APP-1")]))
    return refresh.stored_graph(db, registry.enabled(config, db, NOW))[0]


def test_a_plugin_publishes_only_the_kinds_it_declares_and_each_ref_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    graph = _graph_with(_Unruly(), monkeypatch)
    assert [e.title for e in graph.of_kind("ticket")] == ["APP-1 Do the thing"]
    assert [e.title for e in graph.of_kind("pull_request")] == [pr(1).title]  # github's own
    assert graph.of_kind("thing") == []
    assert "jira published ticket https://jira/browse/APP-1 twice" in caplog.text
    assert f"jira published pull_request {pr(1).url}, a kind it doesn't declare" in caplog.text
    assert "jira published thing 1, a kind it doesn't declare" in caplog.text


class _Narrowed(JiraPlugin):
    """Jira, finding only OPS keys in text."""

    def linking_patterns(self, req: ContextRequest[JiraConfig]) -> dict[str, str | None]:
        return {"ticket": r"\bOPS-\d+\b"}


def test_a_plugin_can_narrow_its_linking_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _graph_with(_Narrowed(), monkeypatch)
    assert graph.named("APP-1 and OPS-2") == [Ref(kind="ticket", id="https://jira/browse/OPS-2")]


class _Prepared(JiraPlugin):
    """Jira, adding a Look link to PR items from work it does once per page."""

    prepared = 0

    def item_actions(self, req: GraphRequest[JiraConfig]) -> ItemAdditionsFor:
        _Prepared.prepared += 1
        prs = {e.ref.id for e in req.graph.of_kind("pull_request")}

        def actions(each: ItemRequest) -> ItemAdditions:
            pr = each.item.subject("pull_request")
            if pr is None or pr.id not in prs:
                return ItemAdditions()
            look = Action(name="", label="Look", fields={}, href=f"/look?pr={pr.id}")
            return ItemAdditions(actions=[look])

        return actions


def test_a_plugin_works_out_its_item_actions_once_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = [_Prepared() if p.name == "jira" else p for p in registry.PLUGINS]
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    mine = [pr(1, merge_state="DIRTY"), pr(2, merge_state="DIRTY")]
    built = dashboard(github=snapshot(mine=mine), jira_site="https://j")
    assert _Prepared.prepared == 1
    looks = [a.href for i in built.panel("github.conflicts").items for a in i.actions if a.href]
    assert looks == [f"/look?pr={pr(1).url}", f"/look?pr={pr(2).url}"]


def test_today_is_the_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(localtime, "zone", timezone(timedelta(hours=-7)))  # Pacific, in summer
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


def _named(name: str) -> Plugin[BaseModel, BaseModel]:
    class Named(Plugin[BaseModel, BaseModel]):
        pass

    Named.name = name
    return Named()


RESERVED = ["core", "act_now", "settings", "serve", "teams", "act"]
"""Some of core's names: an owner, a page, a command, a config key, a group key."""


def test_plugin_names_are_each_their_own_and_none_is_cores() -> None:
    registry.check_plugins(registry.PLUGINS)  # the built-in ones are fine
    clashes = [[*registry.PLUGINS, registry.PLUGINS[0]], *([_named(n)] for n in RESERVED)]
    for clash in clashes:
        with pytest.raises(RuntimeError, match="named git|core's"):
            registry.check_plugins(clash)
    with pytest.raises(RuntimeError, match="lower-case"):
        registry.check_plugins([_named("My-Plugin")])


def test_the_page_says_who_i_am_in_each_tool_that_knows() -> None:
    built = dashboard(github=snapshot())  # github knows my login
    assert built.identities == [Identity(tool="GitHub", login=ME)]
    assert dashboard(local=local_snapshot()).identities == []  # nothing fetched yet


class _Relating(JiraPlugin):
    """Jira, relating its ticket to PR 1, and (wrongly) github's PR to its ticket."""

    def relate(self, req: GraphRequest[JiraConfig]) -> list[tuple[Ref, Ref]]:
        [ticket] = req.graph.of_kind("ticket")
        the_pr = Ref(kind="pull_request", id=pr(1).url)
        return [(ticket.ref, the_pr), (the_pr, ticket.ref)]


def test_a_plugin_relates_only_its_own_entities(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    graph = _graph_with(_Relating(), monkeypatch)
    [ticket] = graph.of_kind("ticket")
    assert Ref(kind="pull_request", id=pr(1).url) in ticket.refs
    assert f"jira related pull_request {pr(1).url}, which it doesn't publish" in caplog.text
