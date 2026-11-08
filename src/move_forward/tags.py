"""`#tags`: put on an entity by a `#tag` in its title, or by hand (`TagRepository`), they relate
everything carrying them. Core publishes a `tag` entity for each (`kinds.TAG`), pointing at what
carries it, and points at it every entity whose text (title or `mentions`) names it, as
"Garbage day" names `#garbage-day`. A tag's words are separated by `-` or `_`, and text names it
however it spaces, hyphenates or cases them. Tags aren't units of work: ice, priorities and Act
now's joins don't pass through them."""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from itertools import pairwise

from .domain import KindModel
from .graph import Entity, KindSpec, Ref
from .kinds import TAG

_TAG = re.compile(r"(?<![\w#&])#(\w[\w-]*)")
"""A tag in text: `#` and a word, not inside one (`a#b`), nor an HTML entity (`&#39;`)."""
_SEPARATORS = re.compile(r"[\s_-]+")
_WORDS = re.compile(r"[^\W_]+")
"""The words text names a tag by; `_` separates them, as `-` and spaces do."""


class TagAttrs(KindModel):
    """A `tag`: by its handle (`garbage-day`)."""

    handle: str


SPEC = KindSpec(TagAttrs, tags_from_title=False)


def handle(text: str) -> str:
    """The tag `text` names: its words, lower-case and NFC, joined by `-`; `Garbage_Day` is
    `garbage-day`."""
    text = unicodedata.normalize("NFC", text).lower()
    return "-".join(word for word in _SEPARATORS.split(text) if word)


def tags(text: str) -> list[str]:
    """The tags in `text`, by handle, in order, once each. A tag needs a letter: `#7` is a PR's
    number."""
    found = (handle(m[1]) for m in _TAG.finditer(unicodedata.normalize("NFC", text)))
    return list(dict.fromkeys(t for t in found if any(c.isalpha() for c in t)))


def named(text: str, handles: Mapping[str, Mapping[tuple[str, ...], str]]) -> list[str]:
    """The tags `text` names by their words, consecutive and separated only by spaces, `-` or
    `_`, ignoring case, the longest at each word first: "garbage day" names `garbage-day`
    before `garbage`. `handles` are the tags by first word, then by all their words."""
    text = unicodedata.normalize("NFC", text).lower()
    words = list(_WORDS.finditer(text))
    found: list[str] = []
    i = 0
    while i < len(words):
        by_words = handles.get(words[i][0], {})
        match = next(
            (
                (tag, len(key))
                for key, tag in sorted(by_words.items(), key=lambda kv: -len(kv[0]))
                if tuple(w[0] for w in words[i : i + len(key)]) == key
                and all(
                    _SEPARATORS.fullmatch(text[a.end() : b.start()])
                    for a, b in pairwise(words[i : i + len(key)])
                )
            ),
            None,
        )
        if match is None:
            i += 1
            continue
        found.append(match[0])
        i += match[1]
    return list(dict.fromkeys(found))


def _by_words(handles: Iterable[str]) -> dict[str, dict[tuple[str, ...], str]]:
    found: dict[str, dict[tuple[str, ...], str]] = {}
    for tag in handles:
        key = tuple(_WORDS.findall(tag))
        if key:
            found.setdefault(key[0], {})[key] = tag
    return found


def tagged(
    entities: list[Entity],
    specs: Mapping[str, KindSpec],
    added: Mapping[Ref, list[str]] | None = None,
) -> list[Entity]:
    """The entities, each pointing too at the tags its text names (but one it carries), then a
    `tag` per tag on any of them, in its title (where its kind's `tags_from_title`) or `added`
    by hand, pointing at what carries it: open while one of those is."""
    carrying: dict[str, list[Entity]] = {}
    for entity in entities:
        spec = specs.get(entity.ref.kind)
        own = tags(entity.title) if spec is None or spec.tags_from_title else []
        for tag in dict.fromkeys([*own, *(added or {}).get(entity.ref, [])]):
            carrying.setdefault(tag, []).append(entity)
    if not carrying:
        return entities
    by_words = _by_words(carrying)
    carriers = {tag: {c.ref for c in found} for tag, found in carrying.items()}
    result: list[Entity] = []
    for entity in entities:
        names = [
            Ref(kind=TAG, id=tag)
            for tag in dict.fromkeys(
                t for text in [entity.title, *entity.mentions] for t in named(text, by_words)
            )
            if entity.ref not in carriers[tag]
        ]
        refs = list(dict.fromkeys([*entity.refs, *names]))
        result.append(entity.model_copy(update={"refs": refs}) if names else entity)
    return result + [
        Entity(
            ref=Ref(kind=TAG, id=tag),
            title=f"#{tag}",
            refs=[c.ref for c in found],
            attrs={"handle": tag},
            lifecycle="open" if any(c.lifecycle == "open" for c in found) else "done",
        )
        for tag, found in carrying.items()
    ]
