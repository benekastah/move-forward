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


def _modules(file: Path, node: ast.ImportFrom | ast.Import) -> list[list[str]]:
    """The modules an import in `file` reaches, each as its parts below `move_forward` (an
    `import name` of `from package import name` taken as the module it may be)."""
    if isinstance(node, ast.Import):
        return [a.name.split(".")[1:] for a in node.names if a.name.startswith("move_forward.")]
    if node.level == 0:
        if not (node.module or "").startswith("move_forward."):
            return []
        base = (node.module or "").split(".")[1:]
    else:
        package = file.parent.relative_to(PACKAGE).parts  # e.g. ("plugins", "git")
        up = node.level - 1  # `.` is the file's own package, `...` two packages up
        if up > len(package):
            return []
        base = [*package[: len(package) - up], *(node.module or "").split(".")]
        base = [p for p in base if p]
    if node.module is None:  # `from ... import people`: each name may be a module
        return [[*base, a.name] for a in node.names]
    return [base]


def _forbidden(file: Path, parts: list[str]) -> bool:
    """Whether a plugin's module may not reach `parts`: core past `api`, or another plugin."""
    own = file.parent.relative_to(PACKAGE).parts[1:2]  # the plugin's name
    if not parts or parts[0] == "api":
        return False
    if parts[0] == "plugins":
        return len(parts) > 1 and tuple(parts[1:2]) != own
    return True


def test_plugins_import_core_only_through_the_api_and_never_each_other() -> None:
    reached = [
        f"{file.relative_to(PACKAGE)}: {'.'.join(parts)}"
        for file in sorted((PACKAGE / "plugins").rglob("*.py"))
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom | ast.Import)
        for parts in _modules(file, node)
        if _forbidden(file, parts)
    ]
    assert reached == []


def test_every_way_of_reaching_past_the_api_is_caught() -> None:
    def caught(source: str) -> list[str]:
        file = PACKAGE / "plugins" / "mail" / "x.py"
        [node] = ast.parse(source).body
        assert isinstance(node, ast.ImportFrom | ast.Import)
        return [".".join(parts) for parts in _modules(file, node) if _forbidden(file, parts)]

    assert caught("from ... import people") == ["people"]
    assert caught("from ...people import Contacts") == ["people"]
    assert caught("from move_forward.tags import handle") == ["tags"]
    assert caught("import move_forward.db") == ["db"]
    assert caught("from ..github import plugin") == ["plugins.github"]
    assert caught("from .. import github") == ["plugins.github"]
    assert caught("from ...api import html") == []
    assert caught("from . import client") == []
    assert caught("from ...plugins.mail import client") == []


def test_ruff_bans_every_core_module_in_plugins() -> None:
    """So a plugin importing core's own modules fails `ruff check` as I write it."""
    pyproject = PACKAGE.parents[1] / "pyproject.toml"
    lint = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]
    banned = set(lint["flake8-tidy-imports"]["banned-api"])
    core = {f"move_forward.{f.stem}" for f in PACKAGE.glob("*.py") if f.stem != "__init__"}
    assert core - banned == set()
