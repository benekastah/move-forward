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
from pydantic import BaseModel, RootModel

from move_forward import cli, config, kinds, registry, render, server
from move_forward.domain import KindModel
from move_forward.graph import Entity, KindSpec
from move_forward.kinds import FAMILIES, Family, Person, declaring, is_url
from move_forward.plugin import (
    API_VERSION,
    ChangeChecker,
    Changed,
    CliHandler,
    Collector,
    Command,
    ContextRequest,
    Fetcher,
    Plugin,
    RefRequest,
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


class _Lenient(BaseModel):
    team: str = ""


def test_a_config_model_that_ignores_unknown_keys_is_refused() -> None:
    class Lax(Plugin[_Lenient, KindModel]):
        name = "lax"
        config_model = _Lenient
        api_version = API_VERSION

    with pytest.raises(RuntimeError, match="lax's config_model must reject unknown keys"):
        registry.check_plugins([Lax()])


class _Account(BaseModel):
    host: str = ""


class _Accounts(Strict):
    accounts: dict[str, _Account]


def test_a_config_model_holding_one_that_ignores_unknown_keys_is_refused() -> None:
    """`[lax.accounts.work] hots = ...` would be dropped as surely as a top-level typo."""

    class Lax(Plugin[_Accounts, KindModel]):
        name = "lax"
        config_model = _Accounts
        api_version = API_VERSION

    with pytest.raises(RuntimeError, match="make _Account a Strict"):
        registry.check_plugins([Lax()])


class _Outer(Strict):
    inner: _Inner | None = None


class _Inner(Strict):
    """Naming a model defined after it, which rebuilding `_Outer` doesn't resolve."""

    later: _LaterLoose | None = None


class _LaterLoose(BaseModel):
    host: str = ""


class _Names(RootModel[list[str]]):
    pass


class _WithRoot(Strict):
    names: _Names = _Names([])


def test_a_loose_model_behind_a_held_models_late_type_is_refused() -> None:
    class Lax(Plugin[_Outer, KindModel]):
        name = "lax"
        config_model = _Outer
        api_version = API_VERSION

    with pytest.raises(RuntimeError, match="make _LaterLoose a Strict"):
        registry.check_plugins([Lax()])


def test_a_root_model_held_in_a_config_has_no_keys_to_reject() -> None:
    class Rooted(Plugin[_WithRoot, KindModel]):
        name = "rooted"
        config_model = _WithRoot
        api_version = API_VERSION

    registry.check_plugins([Rooted()])


def test_a_plugin_built_against_another_api_is_told_so_before_its_config_is_checked() -> None:
    class Old(Plugin[_Lenient, KindModel]):
        name = "old"
        config_model = _Lenient
        api_version = API_VERSION - 1

    with pytest.raises(RuntimeError, match="built against API version"):
        registry.check_plugins([Old()])


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


class _Nothing:
    def save(self, snapshot: KindModel) -> None:
        pass

    def load(self) -> KindModel | None:
        return None


class _Collects(Plugin[Strict, KindModel], Collector[Strict, KindModel]):
    name = "collects"
    config_model = Strict
    api_version = API_VERSION

    def collect(self, req: RefreshRequest[Strict]) -> KindModel:
        return KindModel()

    def snapshots(self, req: ContextRequest[Strict]) -> Snapshots[KindModel]:
        return _Nothing()


class _CollectsHalf(Plugin[Strict, KindModel], Collector[Strict, KindModel]):
    name = "half"
    config_model = Strict
    api_version = API_VERSION

    def collect(self, req: RefreshRequest[Strict]) -> KindModel:
        return KindModel()


def test_a_collector_is_one_that_collects_and_keeps_what_it_found() -> None:
    registry.check_plugins([_Collects(), _Plain()])
    assert isinstance(_Collects(), Collector) and not isinstance(_Plain(), Collector)
    with pytest.raises(TypeError, match="abstract method 'snapshots'"):
        _CollectsHalf()  # pyright: ignore[reportAbstractUsage]


class _Checks(_Collects, ChangeChecker[Strict]):
    name = "checks"
    change_check_seconds: ClassVar[int | None] = 30

    def changed(self, req: RefreshRequest[Strict]) -> Changed:
        return Changed()


class _NeverAsked(_Checks):
    name = "never"
    change_check_seconds: ClassVar[int | None] = None


class _ChecksNothing(_Plain, ChangeChecker[Strict]):
    name = "checks_nothing"
    change_check_seconds: ClassVar[int | None] = 30

    def changed(self, req: RefreshRequest[Strict]) -> Changed:
        return Changed()


def test_a_change_checker_collects_and_is_asked() -> None:
    registry.check_plugins([_Checks()])
    with pytest.raises(TypeError, match="checks_nothing is a ChangeChecker but not a Collector"):
        registry.check_plugins([_ChecksNothing()])
    with pytest.raises(RuntimeError, match="never is a ChangeChecker with neither"):
        registry.check_plugins([_NeverAsked()])


class _FetchesNothing(_Plain, Fetcher[Strict]):
    name = "fetches_nothing"

    def fetch(self, req: RefRequest[Strict]) -> list[Entity]:
        return []


class _CantFetch(_Plain):
    name = "cant_fetch"
    kinds: ClassVar[dict[str, KindSpec]] = {"cant_fetch.x": KindSpec(KindModel, fetchable=True)}


def test_a_fetchable_kind_and_a_fetcher_go_together() -> None:
    with pytest.raises(RuntimeError, match="cant_fetch's cant_fetch.x is fetchable, but it isn't"):
        registry.check_plugins([_CantFetch()])
    with pytest.raises(RuntimeError, match="fetches_nothing is a Fetcher, but none of its kinds"):
        registry.check_plugins([_FetchesNothing()])


class _Undocumented(_Plain, Command[Strict]):
    name = "undocumented"

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return lambda req: 0


def test_a_plugins_command_without_a_docstring_is_refused() -> None:
    """Its help would be nothing: a function's docstring isn't inherited."""
    with pytest.raises(RuntimeError, match="undocumented's cli has no docstring"):
        registry.check_plugins([_Undocumented()])
