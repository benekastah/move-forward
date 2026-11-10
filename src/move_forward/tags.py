"""`#tags`: put on an entity by its tool (`Entity.tags`: a PR's or a ticket's labels), by a
`#tag` in its title or `mentions` (out of code), or by hand (`TagRepository`, and the Tag field
core adds to items: `Tags`), they relate everything carrying them. All tags are alike, and any
can be taken off: one I put on for good, one from its tool or text until its plugin next
refreshes (`take_off`). Core publishes a `tag` entity for each (`kinds.TAG`), pointing at what
carries it, and points at it every entity whose text (title or `mentions`) names it, as
"Garbage day" names `#garbage-day`. A tag's words are separated by `-` or `_`, and text names it
however it spaces, hyphenates or cases them. Tags aren't units of work: ice, priorities and Act
now's joins don't pass through them."""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from datetime import datetime
from itertools import pairwise

from . import act_now
from .domain import KindModel
from .graph import Entity, Graph, KindSpec, Ref
from .kinds import TAG
from .page import Action, Item, ItemAdditions
from .repositories import TagRepository

TAG_ACTION = f"{act_now.ACTIONS}/tag"
UNTAG_ACTION = f"{act_now.ACTIONS}/untag"

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


SPEC = KindSpec(TagAttrs, tags_from_text=False)


def handle(text: str) -> str:
    """The tag `text` names: its words, case-folded and NFC, joined by `-`; `Garbage_Day` is
    `garbage-day`."""
    return "-".join(word for word, _, _ in _words(_fold(text)))


def tag_key(text: str) -> str:
    """The tag I typed (`garbage day`, `#Garbage-Day`), by handle; ValueError when it has no
    letter, as `#7` doesn't."""
    found = handle(text)
    if not any(c.isalpha() for c in found):
        raise ValueError(f"{text!r} isn't a tag: give it a word, e.g. #garbage-day")
    return found


_CODE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}(?=[^`\n]*$)|~{3,}).*?(?:^[ \t]*(?P=fence)[`~]*[ \t]*$|\Z)"
    r"|<!--.*?(?:-->|\Z)"
    r"|(?<!`)(?P<ticks>`+)(?!`)(?:(?!\n[ \t]*\n).){0,500}?(?<!`)(?P=ticks)(?!`)",
    re.DOTALL | re.MULTILINE,
)
"""Code in Markdown (a fenced block, closed by a fence as long at a line's start; a code span,
closed by a run of as many backticks, across lines but not a blank one, within 500
characters: past that the opener is taken for a lone backtick)
and HTML comments (a PR template's notes): `#include` or `#fff` there is no tag."""


_LINE_ENDS = re.compile(r"\r\n?")
_TARGETS = ("](", '="', "='")
"""What comes before a `#` that starts a link's target, not a tag."""


def prose(text: str) -> str:
    """`text` without its code (`_CODE`), where a `#` means something else. Line endings are
    made `\n` first: GitHub sends PR bodies edited on the web with `\r\n`."""
    return _CODE.sub(" ", _LINE_ENDS.sub("\n", text))


def tags(text: str) -> list[str]:
    """The tags in `text`, by handle, in order, once each: `#` and words joined by `-` or `_`,
    not inside a word (`a#b`), an HTML entity (`&#39;`), a URL or a link's target (`/#guide`,
    `[x](#testing)`, `href="#top"`). A tag needs a letter: `#7` is a
    PR's number."""
    text = unicodedata.normalize("NFC", text)
    found: list[str] = []
    for i, c in enumerate(text):
        before = text[i - 1] if i else " "
        if c != "#" or _in_word(before) or before in "#&_/" or text[max(i - 2, 0) : i] in _TARGETS:
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


def carried(entity: Entity, spec: KindSpec | None) -> list[str]:
    """The tags `entity` has from its tool and its text, by handle, in order, once each: a `#tag`
    in its title or mentions, out of their code (where its kind's `tags_from_text`), and its
    tool's (`Entity.tags`: labels). Each refresh of its plugin finds them again."""
    texts = [entity.title, *entity.mentions] if spec is None or spec.tags_from_text else []
    found = [t for text in texts for t in tags(prose(text))]
    found += [h for h in map(handle, entity.tags) if any(c.isalpha() for c in h)]
    return list(dict.fromkeys(found))


def tagged(
    entities: list[Entity],
    specs: Mapping[str, KindSpec],
    added: Mapping[Ref, list[str]] | None = None,
    removed: Mapping[Ref, AbstractSet[str]] | None = None,
) -> list[Entity]:
    """The entities, each pointing too at the tags its text names (but one it carries), then a
    `tag` per tag on any of them, pointing at what carries it: open while one of those is. An
    entity carries the tags it has from its tool and text (`carried`) but those `removed` from
    it, and those `added` to it by hand."""
    carrying: dict[str, list[Entity]] = {}
    for entity in entities:
        gone = (removed or {}).get(entity.ref, set[str]())
        found = [t for t in carried(entity, specs.get(entity.ref.kind)) if t not in gone]
        for tag in dict.fromkeys([*found, *(added or {}).get(entity.ref, [])]):
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
                for text in [prose(text) for text in [entity.title, *entity.mentions]]
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


def take_off(repo: TagRepository, graph: Graph, ref: Ref, tag: str, now: datetime) -> bool:
    """Untag: take `tag` off the entity, whatever put it there. Mine is gone for good; one its
    tool or text gives it stays off until the plugin that published it next refreshes, which
    puts it back if the tool or the text still has it. Whether it was on the entity."""
    mine = repo.remove(ref, tag)
    entity = graph.get(ref.kind, ref.id)
    if entity is None or tag not in carried(entity, graph.spec(ref.kind)):
        return mine
    on = (found := graph.get(TAG, tag)) is not None and ref in found.refs
    repo.take_off(ref, tag, (entity.kind or ref.kind).split(".", 1)[0], now)
    return mine or on


def forget_gone(repo: TagRepository, graph: Graph) -> int:
    """Forget the tags I put on, or took off, entities that are gone where gone means done
    (`KindSpec.gone_means_done`: a deleted entry, a ticked-off todo, a mail thread once read),
    so a new one with the same id starts clean. A tag stays on finished work (a merged PR, a
    done ticket): it's a label, not a ranking. How many entities lost their tags."""
    gone = [
        ref
        for ref in dict.fromkeys([*repo.all(), *repo.taken_off()])
        if graph.get(ref.kind, ref.id) is None
        and (spec := graph.spec(ref.kind)) is not None
        and spec.gone_means_done
    ]
    for ref in gone:
        repo.clear(ref)
    return len(gone)


class Tags:
    """The tags on what items are about: shown on them, with buttons to add one and take each
    off."""

    def __init__(self, graph: Graph):
        self._graph = graph
        self._carried: dict[Ref, list[str]] = {}
        for tag in graph.of_kind(TAG):
            for ref in tag.refs:
                self._carried.setdefault(ref, []).append(tag.ref.id)
        self._known = sorted(t.ref.id for t in graph.of_kind(TAG))

    def _subject(self, item: Item) -> Ref | None:
        """What `item` is mainly about, if it can carry tags: None for an item about nothing,
        or about what hides things (ice)."""
        if not item.subjects or self._graph.hides(item.subjects[0].kind):
            return None
        return item.subjects[0]

    def of(self, item: Item) -> list[str]:
        """The tags whose badges `item` shows (`additions`), in their order: what the tag view
        keeps it by."""
        subject = self._subject(item)
        return list(self._carried.get(subject, [])) if subject else []

    def additions(self, item: Item) -> ItemAdditions:
        """A `#tag` badge per tag on what `item` is mainly about, a Tag field to add one, and an
        Untag button per tag. Nothing for an item about nothing, or about what hides things
        (ice)."""
        subject = self._subject(item)
        if subject is None:
            return ItemAdditions()
        fields = {"kind": subject.kind, "id": subject.id}
        on = self.of(item)
        return ItemAdditions(
            badges=[f"#{tag}" for tag in on],
            actions=[
                Action(
                    name=TAG_ACTION,
                    label="Tag",
                    fields=fields,
                    input="#tag",
                    suggestions=self._known,
                    group="Tags",
                ),
                *(
                    Action(
                        name=UNTAG_ACTION,
                        label=f"Untag #{tag}",
                        fields={**fields, "tag": tag},
                        group="Tags",
                    )
                    for tag in on
                ),
            ],
        )
