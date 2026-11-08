"""`#tags`: words in any entity's text (`mentions`) that relate everything carrying them, and
link what names them, as a ticket's key does. Core finds them and publishes a `tag` entity
for each (`kinds.TAG`), pointing at what carries it. A tag's words are separated by `-` or `_`
(`#garbage-day`), and text names it however it spaces, hyphenates or cases them ("Garbage
day")."""

import re
from collections.abc import Iterable

from .domain import KindModel
from .graph import Entity, KindSpec, Ref
from .kinds import TAG

_TAG = re.compile(r"(?<![\w#&])#(\w[\w-]*)")
"""A tag in text: `#` and a word, not inside one (`a#b`), nor an HTML entity (`&#39;`)."""
_SEPARATORS = re.compile(r"[\s_-]+")


class TagAttrs(KindModel):
    """A `tag`: by its handle (`garbage-day`)."""

    handle: str


SPEC = KindSpec(TagAttrs, linking=None)
"""Tags link what's about them; their pattern is the tags there are (`pattern`)."""


def handle(text: str) -> str:
    """The tag `text` names: its words, lower-case, joined by `-`; `Garbage_Day` is
    `garbage-day`."""
    return "-".join(word for word in _SEPARATORS.split(text.lower()) if word)


def tags(text: str) -> list[str]:
    """The tags in `text`, by handle, in order, once each. A tag needs a letter: `#7` is a PR's
    number."""
    found = (handle(m[1]) for m in _TAG.finditer(text))
    return list(dict.fromkeys(t for t in found if any(c.isalpha() for c in t)))


def pattern(handles: Iterable[str]) -> str | None:
    """The pattern of text naming any of the tags: their words as whole words, ignoring case,
    separated by spaces, `-` or `_`; the longest first, so "garbage day" names `garbage-day`,
    not `garbage`. None for no tags."""
    ordered = sorted(set(handles), key=lambda h: (-len(h), h))
    if not ordered:
        return None
    words = (r"[\s_-]+".join(map(re.escape, h.split("-"))) for h in ordered)
    return rf"(?i)(?<!\w)(?:{'|'.join(words)})(?!\w)"


def entities(carriers: Iterable[Entity]) -> list[Entity]:
    """A `tag` per tag in the carriers' mentions, pointing at them: open while one of them is."""
    carrying: dict[str, list[Entity]] = {}
    for carrier in carriers:
        for tag in dict.fromkeys(t for text in carrier.mentions for t in tags(text)):
            carrying.setdefault(tag, []).append(carrier)
    return [
        Entity(
            ref=Ref(kind=TAG, id=tag),
            title=f"#{tag}",
            refs=[c.ref for c in found],
            attrs={"handle": tag},
            lifecycle="open" if any(c.lifecycle == "open" for c in found) else "done",
        )
        for tag, found in carrying.items()
    ]


def handle_named(kind: str, text: str) -> str | None:
    """The tag text naming one stands for (`Graph`'s `handle_named`); None for other kinds."""
    return handle(text) if kind == TAG else None
