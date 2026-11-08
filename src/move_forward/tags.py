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

_SEPARATORS = re.compile(r"[\s_-]+")
_SHORTEST_NAMED = 3
"""A one-word tag shorter than this is named only with its `#`: `#a` isn't every "a"."""


def _in_word(c: str) -> bool:
    """A letter, digit or combining mark: what a tag's words are made of, in any script."""
    return c.isalnum() or unicodedata.category(c)[0] == "M"


def _fold(text: str) -> str:
    """`text` as tags are compared: NFC, case-folded (`STRASSE` is `straße`'s)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def _words(text: str) -> list[tuple[str, int, int]]:
    """The words in `text`, each with where it starts and ends; `_` separates them, as `-`
    and spaces do."""
    found: list[tuple[str, int, int]] = []
    start: int | None = None
    for i, c in enumerate([*text, " "]):
        if _in_word(c):
            start = i if start is None else start
        elif start is not None:
            found.append((text[start:i], start, i))
            start = None
    return found


class TagAttrs(KindModel):
    """A `tag`: by its handle (`garbage-day`)."""

    handle: str


SPEC = KindSpec(TagAttrs, tags_from_title=False)


def handle(text: str) -> str:
    """The tag `text` names: its words, case-folded and NFC, joined by `-`; `Garbage_Day` is
    `garbage-day`."""
    return "-".join(word for word, _, _ in _words(_fold(text)))


def tags(text: str) -> list[str]:
    """The tags in `text`, by handle, in order, once each: `#` and words joined by `-` or `_`,
    not inside a word (`a#b`) nor an HTML entity (`&#39;`). A tag needs a letter: `#7` is a
    PR's number."""
    text = unicodedata.normalize("NFC", text)
    found: list[str] = []
    for i, c in enumerate(text):
        before = text[i - 1] if i else " "
        if c != "#" or _in_word(before) or before in "#&_":
            continue
        end = i + 1
        while end < len(text) and (_in_word(text[end]) or text[end] in "-_"):
            end += 1
        if end > i + 1 and _in_word(text[i + 1]):
            found.append(handle(text[i + 1 : end]))
    return list(dict.fromkeys(t for t in found if any(c.isalpha() for c in t)))


ByWords = Mapping[str, list[tuple[tuple[str, ...], str]]]
"""Tags by their first word: each one's words and handle, the most words first."""


def named(text: str, handles: ByWords) -> list[str]:
    """The tags `text` names by their words, consecutive and separated only by spaces, `-` or
    `_`, ignoring case, the longest at each word first: "garbage day" names `garbage-day`
    before `garbage`."""
    text = _fold(text)
    words = _words(text)
    found: list[str] = []
    i = 0
    while i < len(words):
        match = next(
            (
                (tag, len(key))
                for key, tag in handles.get(words[i][0], [])
                if tuple(w for w, _, _ in words[i : i + len(key)]) == key
                and all(
                    _SEPARATORS.fullmatch(text[a[2] : b[1]])
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


def _by_words(handles: Iterable[str]) -> ByWords:
    found: dict[str, list[tuple[tuple[str, ...], str]]] = {}
    for tag in handles:
        words = tag.split("-")
        if len(words) > 1 or len(tag) >= _SHORTEST_NAMED:
            found.setdefault(words[0], []).append((tuple(words), tag))
    return {word: sorted(tags, key=lambda kt: -len(kt[0])) for word, tags in found.items()}


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
                t
                for text in [entity.title, *entity.mentions]
                for t in [*(h for h in tags(text) if h in carriers), *named(text, by_words)]
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
