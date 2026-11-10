"""Browser notifications about things that changed: what to tell me, when to, and who keeps
track of what I've been told.

A plugin compares its snapshot and the graph from before a refresh with the ones after
(`Plugin.events`) and says what happened as `Event`s; core adds an event for each item with
a notification (`Item.notify`) newly in the plugin's panels (`newly_shown`), and the ones
about the dashboard itself (a plugin that keeps failing). Nothing is on until I subscribe to its kind
(`[notifications] subscribe`, set on the settings page or by `mfd init`): events of kinds I
haven't subscribed to are never stored. Stored events wait until a dashboard tab, with
the browser's permission, asks for them; events about the same thing arrive as one
notification.
"""

import logging
from collections.abc import Collection
from datetime import datetime, timedelta

from pydantic import Field

from .db import CORE, Database
from .domain import Model
from .page import Panel
from .repositories import KvRepository, SnapshotRepository
from .strict import Strict
from .text import shown

log = logging.getLogger(__name__)

DASHBOARD = "dashboard"
"""The prefix of the kinds core's own events have; plugins' are `<plugin name>.`."""
DASHBOARD_KINDS = {
    f"{DASHBOARD}.source_failing": "a plugin can't fetch, for a while",
}
"""The kinds core makes events of, with what each says, for the settings page."""
MAX_DELIVERED_AT_ONCE = 20
"""Distinct things the page gets in one go; the rest wait for its next request."""
MAX_TEXT = 200


class NotificationsConfig(Strict):
    """`[notifications]`: which events to be told about. Nothing is on until subscribed to."""

    subscribe: list[str] = Field(default_factory=list[str])
    """The kinds to be notified of, e.g. `github.pr_mergeable`; the settings page lists them."""
    failing_minutes: int = Field(default=15, ge=1)
    """How long a plugin must fail to fetch before `dashboard.source_failing` says so."""
    keep_hours: int = Field(default=24, ge=1)
    """Events older than this are dropped undelivered, e.g. from a day with no dashboard open."""


class Event(Model):
    """Something that happened, as a plugin or core tells it."""

    kind: str
    """What happened, as the plugin names it: `pr_mergeable`. Stored as `<plugin>.<kind>`."""
    thing: str
    """What it happened to, e.g. a PR's URL: the plugin's events with the same thing are told
    as one."""
    title: str
    """What it's about, e.g. the PR's title."""
    text: str
    """What happened, as a short phrase: "can be merged"."""
    anchor: str = ""
    """The id of the item on the page, from `page.anchor`, for the notification to open."""


class Notification(Model):
    """What the browser shows: the events of one thing, together."""

    title: str
    body: str
    tag: str
    """The thing: a notification with the same tag replaces the one still showing."""
    url: str
    """The dashboard, at the item."""


def qualified(plugin: str, kind: str) -> str:
    """`kind` as stored and subscribed to: prefixed with its plugin's name."""
    return f"{plugin}.{kind}"


class NotificationRepository:
    """Events waiting for a page to ask for them (`notifications`), and when they were told."""

    def __init__(self, db: Database):
        self._db = db

    def add(self, plugin: str, events: list[Event], now: datetime) -> int:
        """Store the events a plugin made, as of `now`; returns how many. Their things are
        the plugin's (`<plugin>:<thing>`), so two plugins' events about one URL are told
        apart."""
        rows = [
            (
                qualified(plugin, e.kind),
                f"{plugin}:{e.thing}",
                e.title,
                e.text,
                e.anchor,
                now.isoformat(),
            )
            for e in events
        ]
        with self._db.transaction() as conn:
            conn.executemany(
                "INSERT INTO notifications (kind, thing, title, text, anchor, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def take(self, now: datetime, keep: timedelta) -> list[Notification]:
        """The events not yet told, one notification per thing, marked told; those older
        than `keep` are dropped. At most `MAX_DELIVERED_AT_ONCE` things are told: every
        pending event about each of them is included, so repeats cannot starve other work."""
        cutoff = (now - keep).isoformat()
        # All at once: two tabs asking together mustn't both be told the same events.
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
            selected = conn.execute(
                "SELECT thing FROM notifications WHERE told_at IS NULL GROUP BY thing"
                " ORDER BY MIN(id) LIMIT ?",
                (MAX_DELIVERED_AT_ONCE,),
            ).fetchall()
            if not selected:
                return []
            places = ", ".join("?" for _ in selected)
            rows = conn.execute(
                f"SELECT * FROM notifications WHERE told_at IS NULL AND thing IN ({places})"
                " ORDER BY id",
                tuple(row["thing"] for row in selected),
            ).fetchall()
            conn.executemany(
                "UPDATE notifications SET told_at = ? WHERE id = ?",
                [(now.isoformat(), row["id"]) for row in rows],
            )
        things: dict[str, list[tuple[str, str, str]]] = {}
        for row in rows:
            things.setdefault(row["thing"], []).append((row["title"], row["text"], row["anchor"]))
        return [_notification(thing, found) for thing, found in things.items()]


def _notification(thing: str, events: list[tuple[str, str, str]]) -> Notification:
    """One thing's events as a notification: its title, and what happened, in order."""
    title = shown(events[0][0])  # a PR's or ticket's title: what a stranger wrote
    texts = list(dict.fromkeys(shown(text) for _, text, _ in events))
    anchor = next((a for *_, a in reversed(events) if a), "")
    return Notification(
        title=title[:MAX_TEXT],
        body=", ".join(texts)[:MAX_TEXT],
        tag=thing,
        url=f"/#{anchor}" if anchor else "/",
    )


def newly_shown(db: Database, plugin: str, panels: list[Panel]) -> list[Event]:
    """An event for each item of the plugin's `panels` with a notification (`Item.notify`)
    that they didn't show at the last refresh, by its kind and thing; none the first time,
    which has nothing to compare with. What they show is kept (`shown_notices`) for the next,
    after a restart too, so an item that leaves and comes back is told again."""
    shown: dict[tuple[str, str], Event] = {}
    for panel in panels:
        for item in panel.items:
            if (notify := item.notify) is None:
                continue
            thing = notify.thing if notify.thing is not None else item.subjects[0].id
            event = Event(
                kind=notify.kind,
                thing=thing,
                title=item.title,
                text=notify.text,
                anchor=item.anchor or "",
            )
            shown.setdefault((notify.kind, thing), event)
    with db.transaction() as conn:
        known = conn.execute("SELECT 1 FROM shown_plugins WHERE plugin = ?", (plugin,)).fetchone()
        before = {
            (row["kind"], row["thing"])
            for row in conn.execute(
                "SELECT kind, thing FROM shown_notices WHERE plugin = ?", (plugin,)
            )
        }
        conn.execute("DELETE FROM shown_notices WHERE plugin = ?", (plugin,))
        conn.executemany(
            "INSERT INTO shown_notices (plugin, kind, thing) VALUES (?, ?, ?)",
            [(plugin, kind, thing) for kind, thing in shown],
        )
        conn.execute("INSERT OR IGNORE INTO shown_plugins (plugin) VALUES (?)", (plugin,))
    return [event for key, event in shown.items() if key not in before] if known else []


def forget_shown(db: Database, keep: Collection[str]) -> None:
    """Forget what the panels of every plugin but `keep` showed: those I've subscribed to
    none of the events of, or that are off. One I subscribe to again starts afresh, telling
    nothing the first time, rather than all that has shown since it was last compared."""
    places = ", ".join("?" for _ in keep)
    where = f"WHERE plugin NOT IN ({places})" if keep else ""
    with db.transaction() as conn:
        conn.execute(f"DELETE FROM shown_notices {where}", tuple(keep))
        conn.execute(f"DELETE FROM shown_plugins {where}", tuple(keep))


def available(plugin_kinds: dict[str, dict[str, str]]) -> dict[str, str]:
    """Every kind I can subscribe to, with what it says: core's, then each enabled plugin's
    (`plugin_kinds`: plugin name to its kinds)."""
    found = dict(DASHBOARD_KINDS)
    for plugin, kinds in plugin_kinds.items():
        found.update({qualified(plugin, kind): text for kind, text in kinds.items()})
    return found


_SOURCE_STATE = "failing:"


def dashboard_events(
    db: Database, config: NotificationsConfig, now: datetime, plugins: Collection[str]
) -> list[Event]:
    """What core itself has to say: a plugin of `plugins` (those enabled) whose fetch has
    failed for `failing_minutes`, once, until it works again. What was said is kept in core's
    kv."""
    kv = KvRepository(db, CORE)
    events: list[Event] = []
    for source, status in SnapshotRepository(db).of(plugins).items():
        key = f"{_SOURCE_STATE}{source}"
        if status.error is None:
            if kv.get(key) is not None:
                kv.set(key, "")
            continue
        state = _state(kv.get(key))
        since = state.since or now
        said = state.said
        if not said and now - since >= timedelta(minutes=config.failing_minutes):
            events.append(
                Event(
                    kind="source_failing",
                    thing=f"source:{source}",
                    title=f"{source} can't fetch",
                    text=f"failing for {config.failing_minutes}+ minutes: {status.error[:100]}",
                )
            )
            said = True
        kv.set(key, _SourceState(since=since, said=said).model_dump_json())
    return events


class _SourceState(Model):
    """What core remembers of a plugin that fails (kv `failing:<plugin>`)."""

    since: datetime | None = None
    """When it began failing."""
    said: bool = False
    """Whether `source_failing` has been made for this failure."""


def _state(raw: str | None) -> _SourceState:
    if not raw:
        return _SourceState()
    try:
        return _SourceState.model_validate_json(raw)
    except ValueError:
        return _SourceState()
