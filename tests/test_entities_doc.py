"""`docs/entities.md` is the contract between plugins: it must say what the plugins' kind
models say."""

import re
from pathlib import Path

from move_forward import registry, tags
from move_forward.kinds import FAMILIES, TAG

DOC = Path(__file__).parent.parent / "docs" / "entities.md"
_ROW_RE = re.compile(r"^\| `([\w.]+)`( \((\w+)\))? \| (\w+)", re.MULTILINE)
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
    declared: dict[str, tuple[str, str | None]] = {TAG: ("core", None)}
    for plugin in registry.BUILTINS:
        for kind, spec in plugin.kinds.items():
            marker = "linking" if spec.linking is not False else "hiding" if spec.hides else None
            declared.setdefault(kind, (plugin.name, marker))  # a family's first publisher
    assert documented == declared


def test_each_kind_documents_exactly_its_models_attributes() -> None:
    """A family's section lists what every publisher's model has, each one's own marked."""
    text = DOC.read_text()
    fields: dict[str, set[str]] = {TAG: set(tags.SPEC.attrs.model_fields)}
    for plugin in registry.BUILTINS:
        for kind, spec in plugin.kinds.items():
            fields.setdefault(kind, set()).update(spec.attrs.model_fields)
    for kind, declared in fields.items():
        section = _section(_section(text, "## Attributes"), f"### `{kind}`")
        cells = _ATTRIBUTE_CELL_RE.findall(section)
        documented = {name for cell in cells for name in re.findall(r"`(\w+)`", cell)}
        assert documented == declared, kind


def test_the_fetched_table_names_every_fetchable_kind_and_what_it_carries() -> None:
    table = _section(DOC.read_text(), "## Fetched on demand")
    documented = {
        kind: set(re.findall(r"`(\w+)`:", attributes))
        for kind, attributes in re.findall(
            r"^\| `([\w.]+)` \|.*\| ([^|]*) \|$", table, re.MULTILINE
        )
    }
    fetchable: dict[str, set[str]] = {}
    for plugin in registry.BUILTINS:
        for kind, spec in plugin.kinds.items():
            if not spec.fetchable:
                continue
            family = FAMILIES.get(kind)
            model = family.fetched if family else None
            fetchable[kind] = set(model.model_fields) if model else set()
    assert documented == fetchable
