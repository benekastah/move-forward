"""Finding the plugins: the built-ins, and other packages' by entry point; and the names no
plugin may take."""

from collections.abc import Iterator
from importlib.metadata import EntryPoint
from typing import Any, ClassVar

import pytest
from conftest import dashboard

from move_forward import cli, config, kinds, refresh, registry, render, server
from move_forward.db import Database
from move_forward.domain import KindModel
from move_forward.graph import Entity, KindSpec, Ref
from move_forward.kinds import FAMILIES, Family, Person, declaring, is_url
from move_forward.plugin import API_VERSION, EntitiesRequest, NamedRequest, Plugin
from move_forward.strict import Strict
from move_forward.testing import NOW, Harness


class LinearConfig(Strict):
    team: str


class LinearPlugin(Plugin[LinearConfig, Any]):
    name: ClassVar[str] = "linear"
    api_version = API_VERSION
    config_model = LinearConfig


class SettingsPlugin(Plugin[LinearConfig, Any]):
    name: ClassVar[str] = "settings"
    api_version = API_VERSION
    config_model = LinearConfig


class OldLinearPlugin(LinearPlugin):
    api_version = 0


class Deal(KindModel):
    """A deal in some CRM: a family no built-in declares."""

    handle: str
    value: int


DEALS = Family("deal", "deal", Deal, is_url)


class HubDeal(Deal):
    stage: str


class HubPlugin(LinearPlugin):
    name: ClassVar[str] = "hub"
    families = (DEALS,)
    kinds: ClassVar[dict[str, KindSpec]] = {"deal": KindSpec(attrs=HubDeal)}


class PipePlugin(LinearPlugin):
    """Another CRM, sharing hub's declaration of the family (as a package both use would)."""

    name: ClassVar[str] = "pipe"
    families = (DEALS,)
    kinds: ClassVar[dict[str, KindSpec]] = {"deal": KindSpec(attrs=Deal)}


class RivalPlugin(LinearPlugin):
    name: ClassVar[str] = "rival"
    families = (Family("deal", "deal", Person, is_url),)


class PersonPlugin(LinearPlugin):
    name: ClassVar[str] = "persons"
    families = (Family("person", "person", Person, is_url),)


class DottedPlugin(LinearPlugin):
    name: ClassVar[str] = "dotted"
    families = (Family("crm.deal", "deal", Deal, is_url),)


class OldHubPlugin(HubPlugin):
    api_version = 0


class BorrowerPlugin(LinearPlugin):
    """Publishes deals without declaring the family: whoever else loads first, it's left out."""

    name: ClassVar[str] = "borrower"
    kinds: ClassVar[dict[str, KindSpec]] = {"deal": KindSpec(attrs=Deal)}


class Handleless(KindModel):
    value: int


class HandlelessPlugin(LinearPlugin):
    name: ClassVar[str] = "handleless"
    families = (Family("thing", "thing", Handleless, is_url),)


@pytest.fixture(autouse=True)
def families() -> Iterator[None]:
    """Discovering plugins declares their families, for the process; keep each test's to it."""
    with declaring("test", (), keep=False):
        yield


def _installed(monkeypatch: pytest.MonkeyPatch, *points: tuple[str, str]) -> None:
    """Pretend these packages are installed, each with a plugin entry point."""
    found = [EntryPoint(name, value, registry.ENTRY_POINTS) for name, value in points]

    def entry_points(group: str) -> list[EntryPoint]:
        return found if group == registry.ENTRY_POINTS else []

    monkeypatch.setattr(registry, "entry_points", entry_points)


def test_an_installed_plugin_is_found_in_name_order_and_enabled_by_its_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed(monkeypatch, ("linear", f"{__name__}:LinearPlugin"))
    plugins, errors = registry.discover()
    assert errors == []
    names = [p.name for p in plugins]
    assert "linear" in names and names == sorted(names)
    monkeypatch.setattr(registry, "PLUGINS", plugins)
    loaded = config.Config.model_validate({"email": "me@x", "linear": {"team": "ENG"}})
    assert loaded.section("linear", LinearConfig) == LinearConfig(team="ENG")
    assert "linear" in registry.enabled_names(loaded)


def test_a_plugin_that_wont_load_or_takes_a_name_it_cant_is_left_out_and_said_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed(
        monkeypatch,
        ("broken", "no_such_package_here:Plugin"),
        ("settings", f"{__name__}:SettingsPlugin"),
        ("linear", f"{__name__}:LinearPlugin"),
    )
    plugins, errors = registry.discover()
    assert "linear" in [p.name for p in plugins]
    assert {p.name for p in plugins}.isdisjoint({"broken", "settings"})
    assert [e.split(" is left out: ")[0] for e in errors] == [
        "plugin broken (no_such_package_here:Plugin)",
        f"plugin settings ({__name__}:SettingsPlugin)",
    ]
    assert "plugin name 'settings' is core's" in errors[1]


def test_an_installed_plugin_cant_take_a_built_ins_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, ("github", "move_forward.plugins.git.plugin:GitPlugin"))
    plugins, errors = registry.discover()
    assert [p.name for p in plugins] == sorted(p.name for p in registry.BUILTINS)
    assert errors and "two plugins are named git" in errors[0]


def test_the_reserved_names_are_cores_keys_commands_and_paths() -> None:
    parser, _ = cli._parser()  # pyright: ignore[reportPrivateUsage]
    [commands] = [a for a in parser._actions if a.choices and "serve" in a.choices]  # pyright: ignore[reportPrivateUsage]
    core_commands = set(commands.choices or {}) - {p.name for p in registry.PLUGINS}
    assert core_commands <= registry.RESERVED_NAMES
    assert set(config.Config.model_fields) <= registry.RESERVED_NAMES
    assert server.RESERVED_PATHS <= registry.RESERVED_NAMES


def test_a_plugin_built_against_another_api_version_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed(monkeypatch, ("linear", f"{__name__}:OldLinearPlugin"))
    plugins, errors = registry.discover()
    assert "linear" not in [p.name for p in plugins]
    said = f"linear is built against API version 0; this dashboard's is {API_VERSION}"
    assert errors == [f"plugin linear ({__name__}:OldLinearPlugin) is left out: {said}"]


def test_the_built_ins_are_built_against_this_api() -> None:
    assert {p.api_version for p in registry.BUILTINS} == {API_VERSION}


def test_the_page_says_which_installed_plugins_are_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "DISCOVERY_ERRORS", ["plugin linear (x:Y) is left out: boom"])
    built = dashboard()
    assert built.problems == ["plugin linear (x:Y) is left out: boom"]
    assert "plugin linear (x:Y) is left out: boom" in render.dashboard_page(built, set())


def test_installed_plugins_declare_a_family_core_doesnt_know(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _installed(monkeypatch, ("hub", f"{__name__}:HubPlugin"), ("pipe", f"{__name__}:PipePlugin"))
    plugins, errors = registry.discover()
    assert errors == []
    assert {"hub", "pipe"} <= {p.name for p in plugins}
    assert FAMILIES["deal"] is DEALS
    assert kinds.fits("deal", "https://crm.example/deals/1")
    assert not kinds.fits("deal", "1")


@pytest.mark.parametrize(
    ("plugin", "said"),
    [
        ("RivalPlugin", "rival's family deal is declared differently by hub"),
        ("PersonPlugin", "persons's family person is declared differently by core"),
        ("DottedPlugin", "dotted's family 'crm.deal' isn't named like one"),
        ("OldHubPlugin", "hub is built against API version 0"),
        ("BorrowerPlugin", "borrower's kind deal is a family it doesn't declare"),
        ("HandlelessPlugin", "handleless's family thing's model has no handle"),
    ],
)
def test_a_plugin_whose_families_cant_be_declared_is_left_out_and_declares_none(
    monkeypatch: pytest.MonkeyPatch, plugin: str, said: str
) -> None:
    first = (
        [("hub", f"{__name__}:HubPlugin")] if plugin in ("RivalPlugin", "BorrowerPlugin") else []
    )
    _installed(monkeypatch, *first, ("x", f"{__name__}:{plugin}"))
    before = dict(FAMILIES)
    plugins, errors = registry.discover()
    [error] = errors
    assert said in error
    builtins = {p.name for p in registry.BUILTINS}
    assert {p.name for p in plugins} == (builtins | {"hub"} if first else builtins)
    assert dict(FAMILIES) == (before | {"deal": DEALS} if first else before)


class Tag(KindModel):
    handle: str


class TaggerPlugin(LinearPlugin):
    """Tags, written `garbage-day`, that text names however it spaces and cases them."""

    name: ClassVar[str] = "tagger"
    kinds: ClassVar[dict[str, KindSpec]] = {
        "tagger.tag": KindSpec(attrs=Tag, linking=r"(?i)\bgarbage[\s_-]+day\b"),
        "tagger.note": KindSpec(attrs=Tag),
    }

    def entities(self, req: EntitiesRequest[Any, Any]) -> list[Entity]:
        return [
            Entity(
                ref=Ref(kind="tagger.tag", id="1"),
                title="#garbage-day",
                attrs={"handle": "garbage-day"},
            ),
            Entity(
                ref=Ref(kind="tagger.note", id="n"),
                title="n",
                attrs={"handle": "n"},
                mentions=["GARBAGE  DAY"],
            ),
        ]

    def handle_named(self, req: NamedRequest[Any]) -> str | None:
        return "-".join(req.text.lower().replace("_", " ").replace("-", " ").split())


def test_a_plugins_handle_named_links_text_to_its_entities() -> None:
    graph = Harness(TaggerPlugin(), {"team": "ENG"}).graph()
    assert graph.units(Ref(kind="tagger.note", id="n")) == [Ref(kind="tagger.tag", id="1")]


class BrokenTaggerPlugin(TaggerPlugin):
    def handle_named(self, req: NamedRequest[Any]) -> str | None:
        raise RuntimeError("boom")


def test_a_failing_handle_named_leaves_the_match_as_it_is() -> None:
    graph = Harness(BrokenTaggerPlugin(), {"team": "ENG"}).graph()
    assert graph.units(Ref(kind="tagger.note", id="n")) == [
        Ref(kind="tagger.tag", id="GARBAGE  DAY")
    ]


class GreedyPlugin(LinearPlugin):
    """Answers for every kind, though it publishes none."""

    name: ClassVar[str] = "greedy"

    def handle_named(self, req: NamedRequest[Any]) -> str | None:
        return "not-mine"


def test_only_a_kinds_publishers_say_which_handle_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "PLUGINS", [GreedyPlugin(), BrokenTaggerPlugin()])
    loaded = config.Config.model_validate(
        {"email": "me@x", "greedy": {"team": "A"}, "tagger": {"team": "B"}}
    )
    db = Database(":memory:")
    registry.migrate(loaded, db)
    graph, _ = refresh.stored_graph(db, registry.enabled(loaded, db, NOW))
    assert graph.units(Ref(kind="tagger.note", id="n")) == [
        Ref(kind="tagger.tag", id="GARBAGE  DAY")  # not greedy's "not-mine"
    ]
