"""Priorities: how much I said an entity matters, 0 not important, 1 important, 2 critical.

A priority on an entity counts for the items about it, and about what it's part of or holds:
a PR's items carry its ticket's and its work entry's, a work entry's carry its todos'.
"""

from .act_now import PriorityOf
from .graph import Entity, Graph, Ref
from .repositories import PriorityRepository

TODO = "notes.todo"
"""The kind of a work entry's todo, published by notes."""


def level_of(marks: dict[Ref, int], graph: Graph) -> PriorityOf:
    """The priority on a subject: the highest set on it, the units of work it's part of, and
    the todos of those that are work entries; None when none is."""

    def of(ref: Ref) -> int | None:
        covering = [ref, *graph.units(ref)]
        covering += [t.ref for r in list(covering) for t in graph.related(r, TODO)]
        found = [marks[r] for r in covering if r in marks]
        return max(found) if found else None

    return of


def resolve(graph: Graph, text: str, kind: str | None = None) -> Ref:
    """The entity `text` names, by id or by what `Graph.identify` makes of it, among the
    kinds marked `resolvable` (a PR's URL, a ticket's URL or key, an entry's title, a
    branch's id), or with `kind`, among that kind's (e.g. a
    todo by its id). Raises LookupError when the graph has none, or more than one."""
    kinds = [kind] if kind else [k for k in graph.kinds() if (s := graph.spec(k)) and s.resolvable]
    found = [
        entity.ref for k in kinds if (entity := graph.get(k, graph.identify(k, text))) is not None
    ]
    if len(found) != 1:
        raise LookupError(
            f"{text!r} names {'several entities' if found else 'no entity the dashboard knows'}"
            + ("" if kind else "; give its kind with --kind")
        )
    return found[0]


def _finished(entity: Entity) -> bool:
    """Whether the entity's publisher says its work is over."""
    return entity.lifecycle == "done"


def forget_finished(repo: PriorityRepository, graph: Graph) -> int:
    """Clear the priorities of what's done (`Entity.lifecycle`), or gone where gone means done
    (`KindSpec.gone_means_done`: notes' entries and todos). One of a kind that can be fetched
    (`KindSpec.fetchable`: a PR long closed, a ticket) that the graph lacks is looked up; one
    that can't be found keeps its priority, and so does one of any other kind (a branch can
    be missing while git's last refresh failed). How many were cleared."""
    cleared = 0
    marks = repo.all()
    missing: dict[str, list[Ref]] = {}
    for ref in marks:
        entity = graph.get(ref.kind, ref.id)
        spec = graph.spec(ref.kind)
        if entity is not None:
            done = _finished(entity)
        elif spec is not None and spec.fetchable:
            missing.setdefault(ref.kind, []).append(ref)
            continue
        else:
            done = spec is not None and spec.gone_means_done
        if done:
            repo.clear(ref)
            cleared += 1
    for kind, refs in missing.items():
        fetched = graph.fetch(kind, [r.id for r in refs])
        for ref in refs:
            if (entity := fetched.get(ref.id)) is not None and _finished(entity):
                repo.clear(ref)
                cleared += 1
    return cleared
