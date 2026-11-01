"""The entity graph: the only way plugins learn about each other's data.

Each plugin publishes entities (PRs, tickets, branches, ...) that point at others by `Ref`.
Kinds and their attributes are a published contract, listed in `docs/entities.md`.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping

from pydantic import BaseModel, Field, JsonValue

from .domain import Model

UrlFor = Callable[[str, str], str | None]
"""Asks the plugins for an entity's URL by kind and id, whether or not it's in the graph."""
Fetch = Callable[[str, list[str]], list["Entity"]]
"""Asks the plugins to fetch entities of a kind by id, now; see `Graph.fetch`."""


class Ref(Model):
    """Names an entity: its kind, e.g. "pr", and its id within the kind, e.g. a PR's URL."""

    kind: str
    id: str


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


def _no_url(kind: str, id: str) -> str | None:
    return None


def _no_fetch(kind: str, ids: list[str]) -> list["Entity"]:
    return []


def _named(texts: Iterable[str], patterns: dict[str, re.Pattern[str]]) -> list[Ref]:
    """The linking entities `texts` name by id, in order, once each."""
    return list(
        dict.fromkeys(
            Ref(kind=kind, id=match[0])
            for text in texts
            for kind, pattern in patterns.items()
            for match in pattern.finditer(text)
        )
    )


def _with_mentions(entity: Entity, patterns: dict[str, re.Pattern[str]]) -> Entity:
    """The entity pointing, too, at each linking entity whose id its mentions name."""
    if not entity.mentions or not patterns:
        return entity
    refs = list(dict.fromkeys([*entity.refs, *_named(entity.mentions, patterns)]))
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
    ):
        """`linking` names the kinds that link what they're about (see `related`), each with
        the pattern of its ids, for finding them in entities' `mentions`; None for a kind
        whose entities point at what they link themselves. `hiding` names the kinds whose
        entities hide what they point at (see `hidden`)."""
        self._linking = dict(linking or {})
        self._hiding = set(hiding)
        self._patterns = {k: re.compile(p) for k, p in self._linking.items() if p is not None}
        entities = [_with_mentions(e, self._patterns) for e in entities]
        self._by_ref: dict[Ref, Entity] = {}
        self._by_kind: dict[str, list[Entity]] = defaultdict(list)
        self._pointing_at: dict[Ref, list[Entity]] = defaultdict(list)
        self._url_for = url_for
        self._fetch = fetch
        for entity in entities:
            if entity.ref in self._by_ref:
                continue  # a plugin bug, which `build_graph` logs: the first one stands
            ref = entity.ref
            self._by_ref[ref] = entity
            self._by_kind[ref.kind].append(entity)
            for target in entity.refs:
                self._pointing_at[target].append(entity)

    def get(self, kind: str, id: str) -> Entity | None:
        return self._by_ref.get(Ref(kind=kind, id=id))

    def of_kind(self, kind: str) -> list[Entity]:
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
        entity = self._by_ref.get(ref)
        linkers = [r for r in entity.refs if r.kind in self._linking] if entity else []
        linkers += [n.ref for n in neighbours if n.ref.kind in self._linking]
        if ref.kind in self._linking:
            linkers.append(ref)
        candidates = list(neighbours)
        for linker in dict.fromkeys(linkers):
            candidates += self._neighbours(linker)
            if linker in self._by_ref:
                candidates.append(self._by_ref[linker])
        found: dict[Ref, Entity] = {}
        for candidate in candidates:
            if candidate.ref.kind == kind and candidate.ref != ref:
                found.setdefault(candidate.ref, candidate)
        return list(found.values())

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
        """The linking entities `text` names by id, as an entity's `mentions` would find them,
        whether or not they're in the graph: e.g. the tickets a todo names by key. For a
        plugin to find another's ids in text without knowing their pattern."""
        return _named([text], self._patterns)

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

    def view_one[V: BaseModel](self, kind: str, id: str, model: type[V]) -> V | None:
        """One entity as `model` (see `view`); None when the graph doesn't have it."""
        entity = self.get(kind, id)
        return _as(entity, model) if entity else None


def _as[V: BaseModel](entity: Entity, model: type[V]) -> V:
    fields = {"id": entity.ref.id, "title": entity.title, "url": entity.url, "state": entity.state}
    return model.model_validate({**entity.attrs, **fields})


def attrs(model: BaseModel) -> dict[str, JsonValue]:
    """An entity's attributes from its kind's model: the fields set, as JSON values."""
    return model.model_dump(mode="json", exclude_unset=True)
