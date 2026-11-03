"""Priorities: how much I said an entity matters, 0 not important, 1 important, 2 critical.

A priority on an entity counts for the items about it, and about what it's part of or holds:
a PR's items carry its ticket's and its work entry's, a work entry's carry its todos'.
"""

from .act_now import PriorityOf
from .graph import Entity, Graph, Ref
from .repositories import PriorityRepository

TODO = "todo"
"""The kind of a work entry's todo, published by notes."""
_DONE_STATES = {"merged", "closed", "done"}


def level_of(marks: dict[Ref, int], graph: Graph) -> PriorityOf:
    """The priority on a subject: the highest set on it, the units of work it's part of, and
    the todos of those that are work entries; None when none is."""

    def of(ref: Ref) -> int | None:
        covering = [ref, *graph.units(ref)]
        covering += [t.ref for r in list(covering) for t in graph.related(r, TODO)]
        found = [marks[r] for r in covering if r in marks]
        return max(found) if found else None

    return of


RESOLVED_KINDS = ("pr", "ticket", "entry", "branch")
"""The kinds a priority's entity is looked for in when its kind isn't given."""


def resolve(graph: Graph, text: str, kind: str | None = None) -> Ref:
    """The entity `text` names: a PR's URL, a ticket's key, an entry's title or a branch's id
    (or, with `kind`, an entity of that kind, e.g. a todo by its id). Raises LookupError when
    the graph has none, or more than one."""
    found = [
        entity.ref
        for k in ([kind] if kind else RESOLVED_KINDS)
        if (entity := graph.get(k, text)) is not None
    ]
    if len(found) != 1:
        raise LookupError(
            f"{text!r} names {'several entities' if found else 'no entity the dashboard knows'}"
            + ("" if kind else "; give its kind with --kind")
        )
    return found[0]


def _finished(entity: Entity) -> bool:
    """Whether the entity says it's done: a PR merged or closed, a ticket in a done status
    category, a todo ticked off."""
    if entity.ref.kind == "ticket":
        return entity.attrs.get("status_category") == "done"
    return entity.state in _DONE_STATES


def forget_finished(repo: PriorityRepository, graph: Graph) -> int:
    """Clear the priorities of what's done, or gone: a merged or closed PR, a ticket in a done
    status category, a todo ticked off, and an entry, todo or branch the graph no longer has.
    A PR or ticket the graph lacks (long closed, or its tool is down) is looked up; one that
    can't be found keeps its priority. How many were cleared."""
    cleared = 0
    marks = repo.all()
    missing: dict[str, list[Ref]] = {}
    for ref in marks:
        entity = graph.get(ref.kind, ref.id)
        if entity is not None:
            done = _finished(entity)
        elif ref.kind in ("pr", "ticket"):
            missing.setdefault(ref.kind, []).append(ref)
            continue
        else:
            done = ref.kind in ("entry", TODO, "branch")
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
