"""`move_forward.api` is what plugins may use of core, and all they use."""

import ast
import importlib
import tomllib
from pathlib import Path

import move_forward

PACKAGE = Path(move_forward.__file__).parent
API_MODULES = [
    "api",
    "api.html",
    "api.shell",
    "api.http",
    "api.localtime",
    "api.keychain",
    "api.kinds",
    "api.ids",
    "api.tags",
]


def test_every_name_the_api_offers_resolves() -> None:
    for name in API_MODULES:
        module = importlib.import_module(f"move_forward.{name}")
        exported: list[str] = module.__all__
        assert exported, name
        for attribute in exported:
            assert hasattr(module, attribute), f"{name}.{attribute}"


def _core_import(file: Path, node: ast.ImportFrom) -> str | None:
    """The core module a plugin's `from ... import` reaches past `api`, or None."""
    if node.level == 0:
        module = node.module or ""
        if not module.startswith("move_forward."):
            return None
        parts = module.split(".")[1:]
    else:
        package = file.parent.relative_to(PACKAGE).parts  # e.g. ("plugins", "git")
        up = node.level - 1  # `.` is the file's own package, `...` two packages up
        if up > len(package):
            return None
        parts = [*package[: len(package) - up], *(node.module or "").split(".")]
        parts = [p for p in parts if p]
    if not parts or parts[0] in ("api", "plugins"):
        return None
    return ".".join(parts)


def test_plugins_import_core_only_through_the_api() -> None:
    reached: list[str] = []
    for file in sorted((PACKAGE / "plugins").rglob("*.py")):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (core := _core_import(file, node)):
                reached.append(f"{file.relative_to(PACKAGE)}: {core}")
            if isinstance(node, ast.Import):
                reached += [
                    f"{file.relative_to(PACKAGE)}: {a.name}"
                    for a in node.names
                    if a.name.startswith("move_forward.")
                    and not a.name.startswith(("move_forward.api", "move_forward.plugins"))
                ]
    assert reached == []


def test_ruff_bans_every_core_module_in_plugins() -> None:
    """So a plugin importing core's own modules fails `ruff check` as I write it."""
    pyproject = PACKAGE.parents[1] / "pyproject.toml"
    lint = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]
    banned = set(lint["flake8-tidy-imports"]["banned-api"])
    core = {f"move_forward.{f.stem}" for f in PACKAGE.glob("*.py") if f.stem != "__init__"}
    assert core - banned == set()
