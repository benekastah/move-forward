"""What moved in each plugin's tool, as its change checks (`Plugin.changed`) say: core keeps
it, rather than each plugin in its kv.

A check's answer is kept as it's taken, numbered after the last: its fingerprint, for the next
to be compared with, and the subjects it says moved, pending until a collect that started
after it stores. The plugin is asked again as each of its collects starts, so the next check
compares with what the collect read, and what moves while it runs, even what the collect was
given, stays pending for the next. A check asks for a refresh whenever it says something moved,
pending or not, as a subject moving again after a collect failed is news too. The
full-collect timer is here as well: a collect is a full one when it's the plugin's first since
the dashboard started (so a changed config is applied everywhere), when its `refresh_seconds`
have passed since the last full collect that stored, when something moved that no subject
names, and when nothing's pending to narrow it to."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from .graph import Graph
from .plugin import Changed, Previous, RefreshRequest
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
    """Whether to fetch everything (`RefreshRequest.full`)."""
    subjects: frozenset[str]
    """What moved since a collect last stored (`RefreshRequest.subjects`)."""
    at: datetime
    """When the collect started: the last full collect's time, once a full one stores."""
    seen: int
    """The number of the last check's answer taken as the collect started: what that one
    and those before it said moved is settled once the collect stores."""


class ChangeChecks:
    """The plugins' change checks, kept in core's tables, the graph each check was given, kept
    here for the next (`Previous.graph`), and the plugins that had a full
    collect since this started: one per server, or per `mfd refresh`. A plugin may be checked
    while it collects (a collect past its timeout, or a check that started just before): what
    a check keeps then stays pending, as the collect settles only what was taken before it
    started."""

    def __init__(self) -> None:
        self._graphs: dict[str, Graph] = {}
        """The graph each plugin's last check that didn't fail was given."""
        self._full: set[str] = set()
        """The plugins whose full collect stored since this started."""

    def check(self, e: Enabled, graph: Graph) -> Changed | None:
        """Ask the plugin what moved, and keep the answer; the answer when it says something
        moved (a fingerprint other than the last, any subject, or `Changed.everything`), for a
        refresh, else None. Raises when the check does, or answers with something that isn't a
        `Changed`: then nothing is kept, and the next check compares with the same as this one
        did."""
        repo = ChangeRepository(e.ctx.db)
        held = repo.get(e.name)
        answer = e.plugin.changed(
            RefreshRequest(
                e.ctx,
                graph,
                previous=Previous(
                    held.checked_at, held.fingerprint, graph=self._graphs.get(e.name)
                ),
            )
        )
        if not isinstance(cast(object, answer), Changed):  # a plugin needn't be type-checked
            raise TypeError(f"changed answered {answer!r}, not a Changed")
        repo.checked(e.name, answer.fingerprint, e.ctx.now, answer.subjects, answer.everything)
        self._graphs[e.name] = graph
        moved = (
            answer.everything
            or bool(answer.subjects)
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
        due = (
            e.name not in self._full
            or held.full_at is None
            or (seconds is not None and (e.ctx.now - held.full_at).total_seconds() >= seconds)
        )
        full = due or held.everything or not held.subjects
        return Taken(full, held.subjects, e.ctx.now, held.checks)

    def stored(self, e: Enabled, taken: Taken) -> None:
        """The collect given `taken` stored: what moved before it started is no longer
        pending, and a full one is the last full collect."""
        ChangeRepository(e.ctx.db).collected(e.name, taken.seen, taken.at if taken.full else None)
        if taken.full:
            self._full.add(e.name)
