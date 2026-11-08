"""The entity graph: the only way plugins learn about each other's data.

Each plugin publishes entities (PRs, tickets, branches, ...) that point at others by `Ref`.
Kinds and their attributes are a published contract, listed in `docs/entities.md`.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from .domain import KindModel, Model
from .kinds import PERSON, fits, personal_email

UrlFor = Callable[[str, str], str | None]
"""Asks the plugins for an entity's URL by kind and id, whether or not it's in the graph."""
HandleFor = Callable[[str, str], str | None]
"""Asks the plugins for an entity's `handle` by kind and id, whether or not it's in the
graph."""
Fetch = Callable[[str, list[str]], list["Entity"]]
"""Asks the plugins to fetch entities of a kind by id, now; see `Graph.fetch`."""


class Ref(Model):
    """Names an entity: its family or kind, e.g. "pull_request" or "git.branch", and its id
    within it, e.g. a PR's URL."""

    kind: str
    id: str

    def __hash__(self) -> int:
        # Pydantic hashes frozen models already, but type checkers can't see that it does.
        return hash((self.kind, self.id))


class Entity(Model):
    ref: Ref
    title: str
    url: str | None = None
    state: str | None = None
    """e.g. "open", "draft", "merged", "closed"; a ticket's status."""
    refs: list[Ref] = Field(default_factory=list)
    """What it points at."""
    mentions: list[str] = Field(default_factory=list)
    """Text that may name linking entities by id, e.g. a PR's branch, title and body; the graph
    adds a ref to each linking entity whose id it finds in it."""
    attrs: dict[str, JsonValue] = Field(default_factory=dict)
    family: str | None = None
    """The family of kinds it's in (a pull request, a ticket), set by core; None for a kind
    that's a plugin's own."""
    kind: str | None = None
    """Its kind's full name, set by core: `github.pull_request` for a family's (whose `ref`
    names the family), else `ref.kind`."""
    lifecycle: Literal["open", "done"] = "open"
    """Whether the work it stands for is over (a PR merged or closed, a ticket resolved), as
    its publisher says; `state` is the tool's own word for it."""


@dataclass(frozen=True)
class KindSpec:
    """What a plugin says of a kind it publishes (`Plugin.kinds`)."""

    attrs: type[KindModel]
    """The model its entities' `attrs` must fit."""
    linking: str | None | Literal[False] = False
    """Whether it links what it's about (see `Graph.related`): False if not; else the pattern
    of its ids, for finding them in other entities' `mentions` (`Plugin.linking_patterns`
    can narrow it as the plugin learns), or None when its entities point at what they link
    themselves."""
    hides: bool = False
    """Whether its entities hide what they point at (notes' ice); see `Graph.hidden`."""
    fetchable: bool = False
    """Whether `Plugin.fetch` can look its entities up when the graph no longer has them, as
    the daily prune does to learn whether long-gone work ended."""
    gone_means_done: bool = False
    """Whether an entity missing from the graph is done: its plugin publishes only open ones
    (notes' todos), and keeps them where only finishing removes them."""
    resolvable: bool = False
    """Whether `mfd priority <id>`, given no kind, looks among its entities by id."""
    tags_from_text: bool = True
    """Whether a `#tag` in its entities' text, their title or `mentions` (a PR's body, out of its
    code), puts the tag on them (see `tags`). False where anyone can write it, as mail's
    senders write its subjects: their text still names tags."""
    fetched_attrs: type[KindModel] | None = None
    """The model the `attrs` of its entities `Plugin.fetch` returns must fit. None: its
    family's (`kinds.Family.fetched`: what the prune reads, whoever publishes), else any of
    `attrs`' fields, each optional."""


def _no_url(kind: str, id: str) -> str | None:
    return None


def _no_fetch(kind: str, ids: list[str]) -> list["Entity"]:
    return []


Handles = Mapping[tuple[str, str], list[Ref]]
"""The entities of each kind by `handle` (the text that names them: a ticket's key)."""


def _handle(entity: Entity) -> str | None:
    handle = entity.attrs.get("handle")
    return handle if isinstance(handle, str) else None


Unknown = Callable[[str, str], Ref]
"""The ref for a kind's handle no entity in the graph has."""


def _named(
    texts: Iterable[str], patterns: dict[str, re.Pattern[str]], handles: Handles, unknown: Unknown
) -> list[Ref]:
    """The linking entities `texts` name, in order, once each: those whose `handle` it is,
    or, for text no entity in the graph has as its handle, `unknown`'s ref for it."""
    return list(
        dict.fromkeys(
            ref
            for text in texts
            for kind, pattern in patterns.items()
            for match in pattern.finditer(text)
            for ref in handles.get((kind, match[0])) or [unknown(kind, match[0])]
        )
    )


def _with_mentions(
    entity: Entity, patterns: dict[str, re.Pattern[str]], handles: Handles, unknown: Unknown
) -> Entity:
    """The entity pointing, too, at each linking entity its mentions name."""
    if not entity.mentions or not patterns:
        return entity
    named = _named(entity.mentions, patterns, handles, unknown)
    refs = list(dict.fromkeys([*entity.refs, *named]))
    return entity.model_copy(update={"refs": refs}) if len(refs) != len(entity.refs) else entity


class Graph:
    """Every enabled plugin's entities, indexed by ref and by the refs pointing at them."""

    def __init__(
        self,
        entities: Iterable[Entity] = (),
        url_for: UrlFor = _no_url,
        fetch: Fetch = _no_fetch,
        linking: Mapping[str, str | None] | None = None,
        hiding: Iterable[str] = (),
        kinds: Mapping[str, KindSpec] | None = None,
        handle_for: HandleFor = _no_url,
    ):
        """`linking` names the kinds that link what they're about (see `related`), each with
        the pattern of its ids, for finding them in entities' `mentions`; None for a kind
        whose entities point at what they link themselves. `hiding` names the kinds whose
        entities hide what they point at (see `hidden`). `kinds` are the publishers' specs
        of their kinds (see `spec`). `url_for` and `handle_for` ask the plugins about an
        entity that may not be in the graph."""
        self._kinds = dict(kinds or {})
        self._linking = dict(linking or {})
        self._hiding = set(hiding)
        self._patterns = {k: re.compile(p) for k, p in self._linking.items() if p is not None}
        entities = list(entities)
        self._handles: dict[tuple[str, str], list[Ref]] = defaultdict(list)
        for entity in entities:
            if (handle := _handle(entity)) is not None:
                self._handles[(entity.ref.kind, handle)].append(entity.ref)
        self._url_for = url_for
        self._handle_for = handle_for
        self._fetch = fetch
        entities = [
            _with_mentions(e, self._patterns, self._handles, self._unknown) for e in entities
        ]
        self._by_ref: dict[Ref, Entity] = {}
        self._by_kind: dict[str, list[Entity]] = defaultdict(list)
        self._pointing_at: dict[Ref, list[Entity]] = defaultdict(list)
        for entity in entities:
            if entity.ref in self._by_ref:
                continue  # a plugin bug, which `build_graph` logs: the first one stands
            ref = entity.ref
            self._by_ref[ref] = entity
            self._by_kind[ref.kind].append(entity)
            if entity.kind and entity.kind != ref.kind:
                self._by_kind[entity.kind].append(entity)
            for target in entity.refs:
                self._pointing_at[target].append(entity)
        self._people_by_email: dict[str, list[Entity]] = defaultdict(list)
        for person in self._by_kind[PERSON]:
            email = person.attrs.get("email")
            if isinstance(email, str) and (address := personal_email(email)):
                self._people_by_email[address].append(person)

    def get(self, kind: str, id: str) -> Entity | None:
        return self._by_ref.get(Ref(kind=kind, id=id))

    def links(self, kind: str) -> bool:
        """Whether entities' mentions are searched for `kind`'s ids: its plugin gave a pattern."""
        return kind in self._patterns

    def of_kind(self, kind: str) -> list[Entity]:
        """The entities of a family (`pull_request`: every publisher's) or of a kind by its
        full name (`github.pull_request`, `git.branch`)."""
        return list(self._by_kind.get(kind, []))

    def _neighbours(self, ref: Ref) -> list[Entity]:
        """Entities `ref` points at (that are in the graph) and entities pointing at it."""
        entity = self._by_ref.get(ref)
        out = [self._by_ref[r] for r in entity.refs if r in self._by_ref] if entity else []
        return out + self._pointing_at.get(ref, [])

    def related(self, ref: Ref, kind: str) -> list[Entity]:
        """Entities of `kind` one hop from `ref` in either direction, or linked with it by a
        linking entity both are next to: a ticket they both mention, a work entry that names
        both. In graph order, once each, never `ref` itself."""
        neighbours = self._neighbours(ref)
        candidates = list(neighbours)
        for linker in self.units(ref):
            candidates += self._neighbours(linker)
            if linker in self._by_ref:
                candidates.append(self._by_ref[linker])
        found: dict[Ref, Entity] = {}
        for candidate in candidates:
            if candidate.ref.kind == kind and candidate.ref != ref:
                found.setdefault(candidate.ref, candidate)
        return list(found.values())

    def units(self, ref: Ref) -> list[Ref]:
        """The units of work `ref` is part of: the entities of linking kinds it points at,
        mentions or is pointed at by (a ticket a PR mentions, the work entry naming it), and
        `ref` itself when it's one. Once each."""
        entity = self._by_ref.get(ref)
        linkers = [r for r in entity.refs if r.kind in self._linking] if entity else []
        linkers += [n.ref for n in self._neighbours(ref) if n.ref.kind in self._linking]
        if ref.kind in self._linking:
            linkers.append(ref)
        return list(dict.fromkeys(linkers))

    def same_person(self, id: str) -> list[Entity]:
        """The `person` entities of the same human as the person `id`, whichever tools publish
        them: those with their email, itself among them. Just itself without an email, or with
        one many people share; nothing when the graph doesn't have them."""
        person = self.get(PERSON, id)
        if person is None:
            return []
        email = person.attrs.get("email")
        address = personal_email(email) if isinstance(email, str) else None
        return list(self._people_by_email[address]) if address else [person]

    def spec(self, kind: str) -> KindSpec | None:
        """What the publisher of `kind` (a plugin, or core for `tag`) says of it; None for a
        kind nobody publishes."""
        return self._kinds.get(kind)

    def kinds(self) -> list[str]:
        """The kinds published: core's `tag`, then the plugins', in plugin order."""
        return list(self._kinds)

    def hides(self, kind: str) -> bool:
        """Whether entities of `kind` hide what they point at, e.g. notes' ice."""
        return kind in self._hiding

    def hidden(self, ref: Ref) -> bool:
        """Whether a hiding entity covers `ref`: points at it, or at a linking entity next to
        it (an iced ticket hides the PRs that mention it, an iced entry the PRs it names)."""
        if self.hides(ref.kind):
            return False
        linkers = [n.ref for n in self._neighbours(ref) if n.ref.kind in self._linking]
        entity = self._by_ref.get(ref)
        linkers += [r for r in entity.refs if r.kind in self._linking] if entity else []
        return any(
            self.hides(e.ref.kind) for r in [ref, *linkers] for e in self._pointing_at.get(r, [])
        )

    def named(self, text: str) -> list[Ref]:
        """The linking entities `text` names (by handle), as an entity's `mentions` would find them,
        whether or not they're in the graph: e.g. the tickets a todo names by key. For a
        plugin to find another's ids in text without knowing their pattern."""
        return _named([text], self._patterns, self._handles, self._unknown)

    def _unknown(self, kind: str, handle: str) -> Ref:
        """A ref to the entity of `kind` with `handle` that the graph doesn't have: by the id
        its plugin gives it (a ticket's URL, from its key), else by the handle itself."""
        url = self._url_for(kind, handle)
        return Ref(kind=kind, id=url if url and url != handle and fits(kind, url) else handle)

    def identify(self, kind: str, id: str) -> str:
        """The id an entity of a family has, for one kept before its family's ids were what
        they are (a ticket's key, now its URL): the one entity with that handle, else the id
        its plugin gives it. `id` itself when it fits already, or nothing knows better."""
        if fits(kind, id):
            return id
        found = {e.ref.id for e in self.by_handle(kind, id)}
        if len(found) == 1:
            return found.pop()
        ref = self._unknown(kind, id)
        return ref.id if fits(kind, ref.id) else id

    def handle(self, kind: str, id: str) -> str:
        """What to call an entity, e.g. a ticket's key: its `handle` if it's in the graph,
        else whatever its plugin makes of the id, else the id."""
        entity = self.get(kind, id)
        if entity is not None and (handle := _handle(entity)) is not None:
            return handle
        return self._handle_for(kind, id) or id

    def by_handle(self, kind: str, handle: str) -> list[Entity]:
        """The entities of `kind` whose `handle` is `handle`: e.g. the tickets keyed `APP-12`,
        one per tracker that has such a key."""
        return [e for ref in self._handles.get((kind, handle), []) if (e := self._by_ref.get(ref))]

    def url_for(self, kind: str, id: str) -> str | None:
        """The entity's URL if it's in the graph, else whatever a plugin can make of it, e.g.
        a ticket link from its key alone."""
        entity = self.get(kind, id)
        if entity and entity.url:
            return entity.url
        return self._url_for(kind, id)

    def fetch(self, kind: str, ids: list[str]) -> dict[str, Entity]:
        """Entities of `kind` by id, fetched now from the plugin that owns the kind, whether or
        not the graph has them: e.g. PRs merged months ago, for the daily prune. Slow (it calls
        the tool), so only for work that runs rarely; ids the plugin can't find are left out."""
        if not ids:
            return {}
        return {e.ref.id: e for e in self._fetch(kind, ids) if e.ref.kind == kind}

    def view[V: BaseModel](self, kind: str, model: type[V]) -> list[V]:
        """The entities of `kind` as `model`: a consumer's own view of the fields it reads, from
        an entity's `id`, `title`, `url`, `state` and attributes. Raises if one doesn't fit, so
        a publisher's change that breaks a consumer fails its tests, not quietly the page."""
        return [_as(entity, model) for entity in self.of_kind(kind)]

    def view_related[V: BaseModel](self, ref: Ref, kind: str, model: type[V]) -> list[V]:
        """`related(ref, kind)`, each as `model` (see `view`)."""
        return [_as(entity, model) for entity in self.related(ref, kind)]

    def view_by_handle[V: BaseModel](self, kind: str, handle: str, model: type[V]) -> list[V]:
        """`by_handle(kind, handle)`, each as `model` (see `view`)."""
        return [_as(entity, model) for entity in self.by_handle(kind, handle)]

    def view_same_person[V: BaseModel](self, id: str, model: type[V]) -> list[V]:
        """`same_person`, each as `model` (see `view`)."""
        return [_as(entity, model) for entity in self.same_person(id)]

    def view_one[V: BaseModel](self, kind: str, id: str, model: type[V]) -> V | None:
        """One entity as `model` (see `view`); None when the graph doesn't have it."""
        entity = self.get(kind, id)
        return _as(entity, model) if entity else None


def _as[V: BaseModel](entity: Entity, model: type[V]) -> V:
    fields = {
        "id": entity.ref.id,
        "title": entity.title,
        "url": entity.url,
        "state": entity.state,
        "lifecycle": entity.lifecycle,
    }
    return model.model_validate({**entity.attrs, **fields})


def attrs(model: BaseModel) -> dict[str, JsonValue]:
    """An entity's attributes from its kind's model: the fields set, as JSON values."""
    return model.model_dump(mode="json", exclude_unset=True)
