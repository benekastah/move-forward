"""What moved in each plugin's tool, as its change checks (`Plugin.changed`) say: core keeps
it, rather than each plugin in its kv.

A check's answer is kept as it's taken: its fingerprint, for the next to be compared with,
and the subjects it says moved, pending until a collect given them stores. The plugin is
asked again as each of its collects starts, so the next check compares with what the collect
read, and what moves while it runs isn't lost; and what's pending survives a collect that
fails without asking for a refresh at every check, as nothing new moved. The full-collect
timer is here too: a collect is a full one when the plugin's `refresh_seconds` have passed
since the last full collect that stored, when something moved that no subject names, and
when nothing's pending to narrow it to."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from .graph import Graph
from .plugin import Changed, ChangedRequest
from .registry import Enabled
from .repositories import ChangeRepository

log = logging.getLogger(__name__)


def checks(e: Enabled) -> bool:
    """Whether the plugin has a change check: `change_check_seconds`, or plugins it
    `watches`."""
    return e.plugin.change_check_seconds is not None or bool(e.plugin.watches)


@dataclass(frozen=True)
class Taken:
    """What a collect is given, as it starts (`ChangeChecks.take`)."""

    full: bool
    """Whether to fetch everything (`CollectRequest.full`)."""
    subjects: frozenset[str]
    """What moved since a collect last stored (`CollectRequest.subjects`)."""
    at: datetime
    """When the collect started: the last full collect's time, once a full one stores."""


class ChangeChecks:
    """The plugins' change checks, kept in core's tables, and the graph each check was given,
    kept here for the next (`ChangedRequest.previous_graph`): one per server. A plugin is
    never checked while it collects (`mfd serve` leaves it out), so what a check keeps can't
    race what a collect settles."""

    def __init__(self) -> None:
        self._graphs: dict[str, Graph] = {}
        """The graph each plugin's last check that didn't fail was given."""

    def check(self, e: Enabled, graph: Graph) -> Changed | None:
        """Ask the plugin what moved, and keep the answer; the answer when something new
        moved (a fingerprint other than the last, a subject not pending yet, or
        `Changed.everything`), for a refresh, else None. Raises when the check does, or answers
        with something that isn't a `Changed`: then nothing is kept, and the next check
        compares with the same as this one did."""
        repo = ChangeRepository(e.ctx.db)
        held = repo.get(e.name)
        answer = e.plugin.changed(
            ChangedRequest(
                e.ctx, graph, self._graphs.get(e.name), held.fingerprint, held.checked_at
            )
        )
        if not isinstance(cast(object, answer), Changed):  # a plugin needn't be type-checked
            raise TypeError(f"changed answered {answer!r}, not a Changed")
        repo.checked(e.name, answer.fingerprint, e.ctx.now, answer.subjects, answer.everything)
        self._graphs[e.name] = graph
        moved = (
            answer.everything
            or bool(answer.subjects - held.subjects)
            or (
                held.fingerprint is not None
                and answer.fingerprint is not None
                and answer.fingerprint != held.fingerprint
            )
        )
        return answer if moved else None

    def take(self, e: Enabled, graph: Graph | None) -> Taken:
        """What a collect of the plugin starting now is given. With `graph` (what the collect
        works from), a plugin with a check is asked first, so what moved up to now is in it,
        and the next check compares with now; a check that fails is logged, and the collect
        goes on with what's pending."""
        if graph is not None and checks(e):
            try:
                self.check(e, graph)
            except Exception:
                log.debug("%s: change check before collecting failed", e.name, exc_info=True)
        held = ChangeRepository(e.ctx.db).get(e.name)
        seconds = e.refresh_seconds
        due = held.full_at is None or (
            seconds is not None and (e.ctx.now - held.full_at).total_seconds() >= seconds
        )
        return Taken(due or held.everything or not held.subjects, held.subjects, e.ctx.now)

    @staticmethod
    def stored(e: Enabled, taken: Taken) -> None:
        """The collect given `taken` stored: what it was given is no longer pending, and a
        full one is the last full collect."""
        ChangeRepository(e.ctx.db).collected(
            e.name, taken.subjects, taken.at if taken.full else None
        )
