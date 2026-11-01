"""Act now: core's short, ranked list of the best next actions, whatever the tools.

Each plugin nominates items through its `act_now` hook, each with `Signals` saying how long
it takes, what it finishes and who's waiting. Core drops what's blocked, scores the rest,
keeps one item per subject and a few per plugin, and says on each item why it's there.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from .domain import Model
from .graph import Ref
from .page import Action, Item, Panel, Signals, age
from .strict import Strict

KEY = "now"
"""The Act now panel's key within core's Act now group."""
ACTIONS = "act_now"
"""The prefix of Act now's own buttons, `act_now/<snooze|dismiss|pin|unpin>`."""
DUE_HORIZON = timedelta(days=7)
"""How far ahead a due date starts to count."""
DUE_MOST = 4.0
"""What a due date adds (times its weight) once it's past; it rises to that over
`DUE_HORIZON`."""
FeedbackKey = tuple[str, str, str]
"""An item's main subject's kind and id, and its verb: what feedback is kept by."""


class Feedback(Model):
    """What I said of an Act now item."""

    action: Literal["snooze", "dismiss", "pin"]
    fingerprint: str
    """The item's when I said it; a snooze or dismissal of an item that changed lapses."""
    until: datetime | None = None
    """When a snooze ends."""


class Weights(Strict):
    """What each signal adds to an item's score."""

    closes: float = 3
    """Doing it finishes something: a PR merged, a ticket closed."""
    minutes: float = 2
    """It takes minutes."""
    hour: float = 1
    """It takes about an hour."""
    waiting: float = 2
    """Someone is waiting on me for it. Each doubling of the days they've waited adds 1 more."""
    importance: float = 3
    """Per level of importance (1 high, 2 critical)."""
    due: float = 1
    """Times the due date's pressure: 0 a week or more ahead, rising to 4 once it's past."""


class ActNowConfig(Strict):
    """`[act_now]`: how core ranks the plugins' nominations."""

    size: int = Field(default=7, ge=1)
    """How many items Act now shows."""
    per_plugin: int = Field(default=3, ge=1)
    """How many of them one plugin may fill, so one busy tool can't take the whole list."""
    weights: Weights = Field(default_factory=Weights)


@dataclass(frozen=True)
class Nomination:
    """An item a plugin put forward for Act now."""

    plugin: str
    item: Item

    @property
    def signals(self) -> Signals:
        return self.item.signals or Signals()

    @property
    def key(self) -> FeedbackKey | None:
        """What feedback on it is kept by; None for an item about nothing, which can't take
        any."""
        subjects = self.item.subjects
        return (subjects[0].kind, subjects[0].id, self.signals.verb) if subjects else None

    @property
    def fingerprint(self) -> str:
        item = self.item
        if item.fingerprint is not None:
            return item.fingerprint
        seen = json.dumps([item.title, item.badges, item.details])
        return hashlib.sha256(seen.encode()).hexdigest()[:16]


def _set_aside(nomination: Nomination, feedback: Feedback | None, now: datetime) -> bool:
    """Snoozed until later, or dismissed, and not changed since."""
    if feedback is None or feedback.fingerprint != nomination.fingerprint:
        return False
    if feedback.action == "dismiss":
        return True
    return feedback.action == "snooze" and feedback.until is not None and feedback.until > now


def _days(since: timedelta) -> float:
    return max(since.total_seconds(), 0) / 86400


def due_pressure(due: datetime, now: datetime) -> float:
    """0 while `due` is `DUE_HORIZON` or more away, rising to `DUE_MOST` as it arrives."""
    left = due - now
    if left >= DUE_HORIZON:
        return 0.0
    return DUE_MOST * (1 - max(left, timedelta()) / DUE_HORIZON)


def score(signals: Signals, weights: Weights, now: datetime | None = None) -> float:
    """Finishing beats starting, quick beats slow, importance counts, someone waiting adds
    urgency (more the longer they've waited), and so does a due date drawing near. Unknown
    effort adds nothing, so an item of unknown size never takes a quick win's place. Without
    `now`, how long someone has waited and how near a date is don't count."""
    total = (
        weights.closes * (signals.closes is not None)
        + weights.minutes * (signals.effort == "minutes")
        + weights.hour * (signals.effort == "hour")
        + weights.waiting * bool(signals.waiting)
        + weights.importance * signals.importance
    )
    if now is not None and signals.waiting and signals.waiting_since is not None:
        total += math.log2(1 + _days(now - signals.waiting_since))
    if now is not None and signals.due is not None:
        total += weights.due * due_pressure(signals.due, now)
    return total


IMPORTANCE_WORDS = {1: "important", 2: "critical"}


def why(signals: Signals, now: datetime | None = None) -> str:
    """The signals that scored, in words: "critical, quick, closes APP-12, alice waiting 2d,
    due in 3d"."""
    parts: list[str] = []
    if signals.importance in IMPORTANCE_WORDS:
        parts.append(IMPORTANCE_WORDS[signals.importance])
    if signals.effort == "minutes":
        parts.append("quick")
    elif signals.effort == "hour":
        parts.append("about an hour")
    if signals.closes:
        parts.append(f"closes {signals.closes}")
    if signals.waiting:
        waited = ""
        if now is not None and signals.waiting_since is not None:
            waited = f" {age(now, signals.waiting_since)}"
        parts.append(f"{', '.join(signals.waiting)} waiting{waited}")
    if now is not None and signals.due is not None and due_pressure(signals.due, now) > 0:
        if signals.due <= now:
            parts.append(f"overdue by {age(now, signals.due)}")
        else:
            parts.append(f"due in {age(signals.due, now)}")
    return ", ".join(parts)


def _subject(nomination: Nomination, n: int) -> tuple[str, str] | Ref:
    """What the item is about, for one item per subject; an item about nothing is its own."""
    subjects = nomination.item.subjects
    return subjects[0] if subjects else (nomination.plugin, str(n))


def panels(
    nominations: list[Nomination],
    config: ActNowConfig | None = None,
    feedback: dict[FeedbackKey, Feedback] | None = None,
    now: datetime | None = None,
    titles: dict[str, str] | None = None,
) -> list[Panel]:
    """Act now's panels: nominations not blocked, snoozed or dismissed, pinned ones first and
    then the best (by score, then the plugin's own rank, then the order they came in), one
    per subject (the others mentioned on it), at most `per_plugin` from one plugin, `size`
    in all, each with buttons to snooze, dismiss or pin it. They're split into a panel per
    plugin (keyed and, by `titles`, titled after it), so they flow across the page; the
    panels come in their best items' order, and each item is badged with its place overall.
    With nothing to act on, one empty panel says so."""
    config = config or ActNowConfig()
    feedback = feedback or {}
    size, per_plugin, weights = config.size, config.per_plugin, config.weights

    def said(nomination: Nomination) -> Feedback | None:
        return feedback.get(nomination.key) if nomination.key else None

    def pinned(nomination: Nomination) -> bool:
        return (f := said(nomination)) is not None and f.action == "pin"

    candidates = [
        (n, nom)
        for n, nom in enumerate(nominations)
        if not nom.signals.blocked and not (now and _set_aside(nom, said(nom), now))
    ]
    candidates.sort(
        key=lambda c: (
            not pinned(c[1]),
            -score(c[1].signals, weights, now),
            -c[1].item.rank,
            c[0],
        )
    )
    kept: dict[tuple[str, str] | Ref, tuple[Nomination, list[str]]] = {}
    for n, nomination in candidates:
        subject = _subject(nomination, n)
        if subject in kept:
            kept[subject][1].append(nomination.signals.verb or nomination.item.title)
        else:
            kept[subject] = (nomination, [])
    per: dict[str, int] = {}
    by_plugin: dict[str, list[Item]] = {}
    place = 0
    for nomination, also in kept.values():
        if place == size:
            break
        if per.get(nomination.plugin, 0) == per_plugin:
            continue
        per[nomination.plugin] = per.get(nomination.plugin, 0) + 1
        shown = _shown(nomination, also, place, size, pinned(nomination), now)
        by_plugin.setdefault(nomination.plugin, []).append(shown)
        place += 1
    if not by_plugin:
        return [Panel(key=KEY, title="Act now", description="Nothing to act on now.", items=[])]
    titles = titles or {}
    return [
        Panel(
            key=plugin,
            title=titles.get(plugin, plugin),
            description="Its next actions, numbered by their place across every tool.",
            items=items,
        )
        for plugin, items in by_plugin.items()
    ]


def _buttons(nomination: Nomination, pinned: bool) -> list[Action]:
    """Snooze until tomorrow, dismiss, and pin or unpin; none for an item about nothing."""
    if nomination.key is None:
        return []
    kind, id, verb = nomination.key
    fields = {"kind": kind, "id": id, "verb": verb, "fingerprint": nomination.fingerprint}

    def button(action: str, label: str) -> Action:
        return Action(name=f"{ACTIONS}/{action}", label=label, fields=fields)

    return [
        button("snooze", "Snooze"),
        button("dismiss", "Dismiss"),
        button("unpin", "Unpin") if pinned else button("pin", "Pin"),
    ]


def _shown(
    nomination: Nomination,
    also: list[str],
    place: int,
    size: int,
    pinned: bool,
    now: datetime | None,
) -> Item:
    """The item as Act now shows it: its verb first, its place overall as a badge, why it's
    here, what else is due on the same subject, and the buttons to set it aside. Ranked by
    its place, since the panels keep their own order."""
    item, signals = nomination.item, nomination.signals
    reason = ("pinned, " if pinned else "") + why(signals, now)
    reason = reason.removesuffix(", ")
    details = [f"why: {reason}"] if reason else []
    details += item.details
    details += [f"also: {', '.join(also)}"] if also else []
    return item.model_copy(
        update={
            "title": f"{signals.verb}: {item.title}" if signals.verb else item.title,
            "details": details,
            "badges": [f"#{place + 1}", *item.badges],
            "rank": size - place,
            "draft": False,
            "parts": [],
            "actions": [*item.actions, *_buttons(nomination, pinned)],
        }
    )
