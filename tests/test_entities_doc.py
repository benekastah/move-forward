"""`docs/entities.md` is the contract between plugins: it must say what the plugins' kind
models say."""

import re
from pathlib import Path

from move_forward import registry

DOC = Path(__file__).parent.parent / "docs" / "entities.md"
_ROW_RE = re.compile(r"^\| `(\w+)`( \((\w+)\))? \| (\w+)", re.MULTILINE)
_ATTRIBUTE_CELL_RE = re.compile(r"^\| ((?:`\w+`(?:, )?)+) \|", re.MULTILINE)


def _section(text: str, heading: str) -> str:
    """From `heading` to the next heading of its level or above."""
    start = text.index(heading + "\n")
    level = heading.split(" ")[0]
    rest = text[start + len(heading) :]
    end = re.search(rf"^#{{1,{len(level)}}} ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def test_the_kinds_table_names_every_kind_its_publisher_and_whether_it_links_or_hides() -> None:
    documented = {
        kind: (publisher, marker or None)
        for kind, _, marker, publisher in _ROW_RE.findall(_section(DOC.read_text(), "## Kinds"))
    }
    declared = {
        kind: (
            plugin.name,
            "linking" if kind in plugin.linking else "hiding" if kind in plugin.hides else None,
        )
        for plugin in registry.PLUGINS
        for kind in plugin.kinds
    }
    assert documented == declared


def test_each_kind_documents_exactly_its_models_attributes() -> None:
    text = DOC.read_text()
    for plugin in registry.PLUGINS:
        for kind, model in plugin.kinds.items():
            section = _section(_section(text, "## Attributes"), f"### `{kind}`")
            cells = _ATTRIBUTE_CELL_RE.findall(section)
            documented = {name for cell in cells for name in re.findall(r"`(\w+)`", cell)}
            assert documented == set(model.model_fields), kind
