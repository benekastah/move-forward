"""The entity graph: the only way plugins learn about each other's data.

Each plugin publishes entities (PRs, tickets, branches, ...) that point at others by `Ref`.
Kinds and their attributes are a published contract, listed in `docs/entities.md`.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable

from pydantic import BaseModel, Field, JsonValue

from .domain import Model

TICKET = "ticket"
"""The kind that links entities indirectly: two entities about one ticket are related."""

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
    attrs: dict[str, JsonValue] = Field(default_factory=dict)


def _no_url(kind: str, id: str) -> str | None:
    return None


def _no_fetch(kind: str, ids: list[str]) -> list["Entity"]:
    return []


def _merge(first: Entity, second: Entity) -> Entity:
    """One entity from two plugins' views of it, e.g. a branch git and git_stack both know.
    The first plugin's fields win; refs and attributes are combined."""
    return first.model_copy(
        update={
            "url": first.url or second.url,
            "state": first.state or second.state,
            "refs": [*first.refs, *(r for r in second.refs if r not in first.refs)],
            "attrs": {**second.attrs, **first.attrs},
        }
    )


class Graph:
    """Every enabled plugin's entities, indexed by ref and by the refs pointing at them."""

    def __init__(
        self, entities: Iterable[Entity] = (), url_for: UrlFor = _no_url, fetch: Fetch = _no_fetch
    ):
        self._by_ref: dict[Ref, Entity] = {}
        self._by_kind: dict[str, list[Entity]] = defaultdict(list)
        self._pointing_at: dict[Ref, list[Entity]] = defaultdict(list)
        self._url_for = url_for
        self._fetch = fetch
        merged: dict[Ref, Entity] = {}
        for entity in entities:
            first = merged.get(entity.ref)
            merged[entity.ref] = _merge(first, entity) if first else entity
        for ref, entity in merged.items():
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
        """Entities of `kind` one hop from `ref` in either direction, or that share a ticket
        with it, e.g. a work entry and the PRs for the ticket it names. In graph order, once
        each, never `ref` itself."""
        found: dict[Ref, Entity] = {}
        entity = self._by_ref.get(ref)
        tickets = [r for r in entity.refs if r.kind == TICKET] if entity else []
        if ref.kind == TICKET:
            tickets.append(ref)
        candidates = self._neighbours(ref)
        for ticket in tickets:
            candidates += self._pointing_at.get(ticket, [])
            if ticket in self._by_ref:
                candidates.append(self._by_ref[ticket])
        for candidate in candidates:
            if candidate.ref.kind == kind and candidate.ref != ref:
                found.setdefault(candidate.ref, candidate)
        return list(found.values())

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
