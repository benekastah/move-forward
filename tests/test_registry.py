"""Finding the plugins: the built-ins, and other packages' by entry point; and the names no
plugin may take."""

from importlib.metadata import EntryPoint
from typing import Any, ClassVar

import pytest
from conftest import dashboard

from move_forward import cli, config, registry, render, server
from move_forward.plugin import API_VERSION, Plugin
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
