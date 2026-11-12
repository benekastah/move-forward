"""`docs/plugin_contract.md` lists the whole plugin contract: it must name every class variable
and property, hook, `Init` field, request, context and answer field, context method and config
marker there is, and nothing that isn't."""

import dataclasses
import re
from pathlib import Path

from move_forward import strict
from move_forward.api import (
    Changed,
    Context,
    ContextRequest,
    Init,
    InputRequest,
    ItemRequest,
    Plugin,
    Previous,
    RefRequest,
    RefreshRequest,
    Request,
)

DOC = Path(__file__).parent.parent / "docs" / "plugin_contract.md"
_ROW_NAME = re.compile(r"^\| `(\w+)`", re.MULTILINE)


def _section(heading: str) -> str:
    """From `heading` to the next heading of its level or above."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(heading + "\n")
    level = heading.split(" ")[0]
    rest = text[start + len(heading) :]
    end = re.search(rf"^#{{1,{len(level)}}} ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _listed(heading: str) -> list[str]:
    """The names in the first column of the tables under `heading`."""
    return _ROW_NAME.findall(_section(heading))


def test_every_class_variable_is_listed() -> None:
    properties = {name for name, value in vars(Plugin).items() if isinstance(value, property)}
    declared = {*Plugin.__annotations__, *properties}
    assert sorted(_listed("## Class variables")) == sorted(declared)


def test_every_hook_is_listed() -> None:
    hooks = {
        name for name, value in vars(Plugin).items() if callable(value) and not name.startswith("_")
    }
    assert sorted(_listed("## Hooks")) == sorted(hooks)


def test_every_init_field_is_listed() -> None:
    assert _listed("## `Init`") == [f.name for f in dataclasses.fields(Init)]


def test_every_request_context_and_answer_field_is_listed() -> None:
    for request in (
        Context,
        Changed,
        ContextRequest,
        Request,
        RefreshRequest,
        RefRequest,
        InputRequest,
        Previous,
        ItemRequest,
    ):
        fields = [f.name for f in dataclasses.fields(request)]
        assert _listed(f"### `{request.__name__}`") == fields, request.__name__


def test_every_context_method_is_listed() -> None:
    methods = {
        name
        for name, value in vars(Context).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }
    listed = re.findall(r"^\| `(\w+)", _section("### `Context`'s methods"), re.MULTILINE)
    assert sorted(listed) == sorted(methods)


def test_every_config_marker_is_listed() -> None:
    markers = [name for name, value in vars(strict).items() if isinstance(value, strict.Marker)]
    assert sorted(_listed("## Config markers")) == sorted(markers)
