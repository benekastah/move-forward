"""Finding the plugins: the built-ins, and other packages' by entry point; and the names no
plugin may take."""

import argparse
import sys
from collections.abc import Iterator
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import dashboard

from move_forward import cli, config, kinds, registry, render, server
from move_forward.domain import KindModel
from move_forward.graph import KindSpec
from move_forward.kinds import FAMILIES, Family, Person, declaring, is_url
from move_forward.plugin import (
    API_VERSION,
    CliHandler,
    ContextRequest,
    Plugin,
    RefreshRequest,
    Snapshots,
)
from move_forward.strict import Strict


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


def test_every_package_in_plugins_is_a_built_in_named_for_it() -> None:
    packages = sorted(
        p.name
        for p in (Path(registry.builtin_plugins.__file__).parent).iterdir()
        if (p / "__init__.py").is_file()
    )
    assert sorted(p.name for p in registry.BUILTINS) == packages


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (None, "has no plugin.py"),
        ("", "defines 0 Plugin subclasses, not one"),
        (
            "class A(Plugin):\n    name = 'a'\nclass B(Plugin):\n    name = 'b'\n",
            "defines 2 Plugin subclasses, not one",
        ),
        ("class A(Plugin):\n    name = 'other'\n", "named 'other', not 'fake_builtin'"),
    ],
)
def test_a_built_in_package_defines_one_plugin_named_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str | None, error: str
) -> None:
    package = tmp_path / "fake_builtin"
    package.mkdir()
    (package / "__init__.py").write_text("")
    if source is not None:
        (package / "plugin.py").write_text(f"from move_forward.plugin import Plugin\n{source}")
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    try:
        with pytest.raises(TypeError, match=error):
            registry.builtin(package.name)
    finally:  # each case imports its own
        for name in ("fake_builtin", "fake_builtin.plugin"):
            sys.modules.pop(name, None)


def test_what_a_built_in_imports_failing_is_said_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "fake_broken"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "plugin.py").write_text("import fake_missing_dependency\n")
    monkeypatch.syspath_prepend(str(tmp_path))  # pyright: ignore[reportUnknownMemberType]
    try:
        with pytest.raises(ModuleNotFoundError, match="fake_missing_dependency"):
            registry.builtin(package.name)
    finally:
        for name in ("fake_broken", "fake_broken.plugin"):
            sys.modules.pop(name, None)


def test_a_plugin_named_for_a_core_tables_prefix_is_refused() -> None:
    class Team(Plugin[Strict, KindModel]):
        name = "team"
        config_model = Strict
        api_version = API_VERSION

    with pytest.raises(RuntimeError, match="would own core's team_channels"):
        registry.check_plugins([Team()])


def test_a_plugin_named_for_a_core_indexs_prefix_is_refused() -> None:
    class Pings(Plugin[Strict, KindModel]):
        name = "pings"
        config_model = Strict
        api_version = API_VERSION

    with pytest.raises(RuntimeError, match="would own core's pings_"):
        registry.check_plugins([Pings()])


class _Plain(Plugin[Strict, KindModel]):
    name = "plain"
    config_model = Strict
    api_version = API_VERSION


class _Collects(Plugin[Strict, KindModel]):
    name = "collects"
    config_model = Strict
    api_version = API_VERSION

    def collect(self, req: RefreshRequest[Strict]) -> KindModel:
        return KindModel()


class _Keeps(Plugin[Strict, KindModel]):
    name = "keeps"
    config_model = Strict
    api_version = API_VERSION

    def snapshots(self, req: ContextRequest[Strict]) -> Snapshots[KindModel]:
        return _Nothing()


class _Nothing:
    def save(self, snapshot: KindModel) -> None:
        pass

    def load(self) -> KindModel | None:
        return None


class _CollectsAndKeeps(_Collects):
    name = "both"

    def snapshots(self, req: ContextRequest[Strict]) -> Snapshots[KindModel]:
        return _Nothing()


def test_a_plugin_collects_when_it_overrides_collect_and_snapshots() -> None:
    registry.check_plugins([_CollectsAndKeeps()])
    assert _CollectsAndKeeps().collects
    assert not _Plain().collects


def test_a_plugin_overriding_collect_without_snapshots_is_refused() -> None:
    with pytest.raises(RuntimeError, match="collects overrides collect but not snapshots"):
        registry.check_plugins([_Collects()])


def test_a_plugin_overriding_snapshots_without_collect_is_refused() -> None:
    with pytest.raises(RuntimeError, match="keeps overrides snapshots but not collect"):
        registry.check_plugins([_Keeps()])


class _Undocumented(_Plain):
    name = "undocumented"

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return lambda req: 0


def test_a_plugins_command_without_a_docstring_is_refused() -> None:
    """Its help would be `Plugin.cli`'s docstring, inherited, or nothing."""
    with pytest.raises(RuntimeError, match="undocumented's cli has no docstring"):
        registry.check_plugins([_Undocumented()])


@pytest.mark.parametrize(
    ("removed", "replacement"),
    [
        ("init_template", "`Init`'s `tables`"),
        ("init_questions", "`Init`, as its `questions`"),
        ("token_help", "`Init`, as its `token_help`"),
        ("url_for", "address(req)"),
        ("refresh_subjects", "req.subjects"),
        ("snapshot_model", "overrides `collect`"),
        ("cli_help", "docstring's first line"),
    ],
)
def test_a_plugin_setting_a_removed_attribute_is_told_what_replaced_it(
    removed: str, replacement: str
) -> None:
    old = type("Old", (_Plain,), {"name": "old", removed: "anything"})()
    with pytest.raises(RuntimeError, match=f"old sets {removed},") as refused:
        registry.check_plugins([old])
    assert replacement in str(refused.value)


def test_every_removed_attribute_is_gone_from_plugin() -> None:
    """Else every plugin would have it, and be refused."""
    assert [name for name in registry.REMOVED_ATTRIBUTES if hasattr(Plugin, name)] == []
