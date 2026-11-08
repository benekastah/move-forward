"""Act now: core's short, ranked list of the best next actions, whatever the tools.

Each plugin nominates items through its `act_now` hook, each with `Signals` saying how long
it takes, what it finishes and who's waiting. Core scores them (a blocked one lower, unless it has sat untouched for a while),
keeps one item per subject, joins an item about a unit of work (a ticket, a work entry) to
the best item about something in it (its PR), keeps a few per plugin, and says on each
item why it's there.
"""

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal

from pydantic import Field, field_validator

from . import localtime
from .domain import Model
from .graph import Ref
from .page import Action, Badge, Item, Link, Panel, SetAside, Signals, age
from .strict import Strict

KEY = "now"
"""The Act now panel's key within core's Act now group."""
ACTIONS = "act_now"
"""The prefix of Act now's own buttons, `act_now/<snooze|dismiss|pin|unpin|opened|priority>`."""
OPENED_FIELD = "opened"
"""The field a plugin's button carries, in Act now, to mark its item opened once the button
has run: the item's `act_now_fields`."""
DUE_HORIZON = timedelta(days=7)
"""How far ahead a due date starts to count, unless `[act_now] due_warning_days` says."""
DUE_MOST = 4.0
"""What a due date adds (times its weight) once it's past; it rises to that over
`DUE_HORIZON`."""
MAX_MEMBERS = 20
"""More items than one Act now item ever joins: a bound on the `also` a button posts."""
Units = Callable[[Ref], list[Ref]]
"""The units of work a subject is part of (see `Graph.units`)."""
PriorityOf = Callable[[Ref], int | None]
"""The priority I set on what a subject is, is part of or contains, 0 to 2; None if none."""
FeedbackKey = tuple[str, str, str]
"""An item's main subject's kind and id, and its task (`Signals.task`): what feedback is kept
by."""


class Feedback(Model):
    """What I said of an Act now item."""

    action: Literal["snooze", "dismiss", "pin", "opened"]
    """`opened` is no choice of mine: I acted on the item elsewhere (a link, a button), so it's
    dimmed until it changes, or for `OPENED_FOR`."""
    fingerprint: str
    """The item's when I said it; a snooze or dismissal of an item that changed lapses."""
    until: datetime | None = None
    """When a snooze ends, or an opened item stops being dimmed."""
    said_at: datetime | None = None
    """When I said it; set when feedback is read back."""


OPENED_FOR = timedelta(hours=24)
"""The longest an item I opened stays dimmed, should it not change: one opened and left
shouldn't stay grey for weeks."""


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
    """Times the due date's pressure: 0 `due_warning_days` or more ahead, rising to 4 once it's
    past."""
    blocked: float = -4
    """A blocked item, until it's been untouched for `blocked_stale_days`."""


RENAMED_KINDS = {
    "pr": "pull_request",
    "branch": "git.branch",
    "repo": "git.repo",
    "stack_branch": "git_stack.stack_branch",
    "entry": "notes.entry",
    "todo": "notes.todo",
    "iced": "notes.iced",
}
"""Kinds by the names they had before plugins' kinds were named for them, to say what a
`snooze_days` key is called now."""


class ActNowConfig(Strict):
    """`[act_now]`: how core ranks the plugins' nominations."""

    size: int = Field(default=7, ge=1)
    """How many items Act now shows."""
    per_plugin: int = Field(default=3, ge=1)
    """How many of them one plugin may fill, so one busy tool can't take the whole list."""
    weights: Weights = Field(default_factory=Weights)
    blocked_stale_days: int = Field(default=3, ge=0)
    """Days a blocked item can go untouched before Act now stops ranking it down, since
    something blocked this long is worth chasing; 0 ranks blocked items down always."""
    due_warning_days: int = Field(default=7, ge=1)
    """How many days ahead a due date starts to raise an item: the pressure rises from 0 at
    this distance to its most when the date arrives."""
    default_snooze_days: int = Field(default=1, ge=1)
    """How many days a snooze lasts, counted in local days: 1 is until tomorrow."""
    snooze_days: dict[str, int] = Field(default_factory=dict)
    """`default_snooze_days` for a kind of subject, e.g. `pull_request = 7` to snooze a PR for
    a week, or `"notes.entry" = 2`. A snooze still ends early when its item changes."""

    snooze_until_hour: int = Field(default=0, ge=0, le=23)
    """The local hour a snooze ends at on its last day: 0 is midnight, 9 shows a snooze made at
    5pm again at 9am the next morning."""

    @field_validator("snooze_days")
    @classmethod
    def _no_old_kinds(cls, value: dict[str, int]) -> dict[str, int]:
        for kind in value:
            if kind in RENAMED_KINDS:
                raise ValueError(f"the kind {kind!r} is called {RENAMED_KINDS[kind]!r} now")
        return value

    def snooze_until(self, kind: str, now: datetime) -> datetime:
        """When a snooze of an item about a `kind` of subject, made at `now`, ends: that many
        local days on, at `snooze_until_hour`."""
        days = max(self.snooze_days.get(kind, self.default_snooze_days), 1)
        local = localtime.local(now)
        return datetime.combine(
            local.date() + timedelta(days=days), time(self.snooze_until_hour), local.tzinfo
        )


def _listed_parts(item: Item) -> list[Item]:
    """The parts an item lists beneath it, with their own buttons: those of an item with
    several. The buttons of one with a single part are on the item itself."""
    return item.parts if len(item.parts) > 1 else []


def _joined_actions(item: Item) -> list[Action]:
    """The buttons an item joined to another puts on that one: those its single part, listed
    beneath it, doesn't carry."""
    shown = item.parts[0].actions if len(item.parts) == 1 else []
    return [action for action in item.actions if action not in shown]


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
        """What feedback on it is kept by; None for an item about nothing, or without a
        task, which can't take any."""
        subjects, task = self.item.subjects, self.signals.task
        return (subjects[0].kind, subjects[0].id, task) if subjects and task else None

    @property
    def fingerprint(self) -> str:
        item = self.item
        if item.fingerprint is not None:
            return item.fingerprint
        badges = [b if isinstance(b, str) else b.text for b in item.badges]
        parts = [part.title for part in _listed_parts(item)]
        seen = json.dumps([item.title, badges, item.details, *([parts] if parts else [])])
        return hashlib.sha256(seen.encode()).hexdigest()[:16]


def no_priority(ref: Ref) -> int | None:
    """No priorities set (without a database)."""
    return None


ContactImportance = Callable[[Ref], int]
"""The importance the contact type of who a subject is about gives it (see `people`)."""


def no_contact_importance(ref: Ref) -> int:
    """No one's contact type counts (without a database)."""
    return 0


def _with_contact_importance(
    nomination: Nomination, contact_importance: ContactImportance
) -> Nomination:
    """The nomination raised to the importance of who it's about; it's never lowered."""
    raised = max((contact_importance(s) for s in nomination.item.subjects), default=0)
    if raised <= nomination.signals.importance:
        return nomination
    signals = nomination.signals.model_copy(update={"importance": raised})
    return Nomination(nomination.plugin, nomination.item.model_copy(update={"signals": signals}))


def prioritized(nomination: Nomination, priority_of: PriorityOf) -> tuple[Nomination, int | None]:
    """The nomination with the highest priority I set on its subjects replacing its plugin's
    importance, and that priority; as it is, and None, when I set none."""
    levels = [p for s in nomination.item.subjects if (p := priority_of(s)) is not None]
    if not levels:
        return nomination, None
    level = max(levels)
    signals = nomination.signals.model_copy(update={"importance": PRIORITY_IMPORTANCE[level]})
    return Nomination(
        nomination.plugin, nomination.item.model_copy(update={"signals": signals})
    ), level


def set_aside(fingerprint: str, feedback: Feedback | None, now: datetime) -> SetAside | None:
    """How the item with `fingerprint` (as Act now nominates it) is set aside, snoozed until
    later or dismissed, if it hasn't changed since; None while it isn't. What Act now and every
    panel go by."""
    if feedback is None or feedback.fingerprint != fingerprint:
        return None
    if feedback.action == "dismiss":
        return "dismiss"
    if feedback.action == "snooze" and feedback.until is not None and feedback.until > now:
        return "snooze"
    return None


def opened_at(nomination: Nomination, feedback: Feedback | None, now: datetime) -> datetime | None:
    """When I opened the item, if it hasn't changed since and that's within `OPENED_FOR`:
    it's shown dimmed, in its place."""
    if feedback is None or feedback.action != "opened" or feedback.said_at is None:
        return None
    if feedback.fingerprint != nomination.fingerprint or now - feedback.said_at >= OPENED_FOR:
        return None
    return feedback.said_at


def _days(since: timedelta) -> float:
    return max(since.total_seconds(), 0) / 86400


def due_pressure(due: datetime, now: datetime, horizon: timedelta = DUE_HORIZON) -> float:
    """0 while `due` is `horizon` or more away, rising to `DUE_MOST` as it arrives."""
    left = due - now
    if left >= horizon:
        return 0.0
    return DUE_MOST * (1 - max(left, timedelta()) / horizon)


def is_stale(signals: Signals, stale_after: timedelta | None, now: datetime | None) -> bool:
    """Whether a blocked item has gone untouched for `stale_after` (None: never)."""
    return (
        stale_after is not None
        and now is not None
        and signals.touched is not None
        and now - signals.touched >= stale_after
    )


def score(
    signals: Signals,
    weights: Weights,
    now: datetime | None = None,
    stale_after: timedelta | None = None,
    horizon: timedelta = DUE_HORIZON,
) -> float:
    """Finishing beats starting, quick beats slow, importance counts, someone waiting adds
    urgency (more the longer they've waited), and so does a due date drawing near. Unknown
    effort adds nothing, so an item of unknown size never takes a quick win's place. Without
    `now`, how long someone has waited and how near a date is don't count. A blocked item
    scores less, unless it has been untouched for `stale_after`."""
    total = (
        weights.closes * (signals.closes is not None)
        + weights.minutes * (signals.effort == "minutes")
        + weights.hour * (signals.effort == "hour")
        + weights.waiting * bool(signals.waiting)
        + weights.importance * signals.importance
        + weights.blocked * (signals.blocked and not is_stale(signals, stale_after, now))
    )
    if now is not None and signals.waiting and signals.waiting_since is not None:
        total += math.log2(1 + _days(now - signals.waiting_since))
    if now is not None and signals.due is not None:
        total += weights.due * due_pressure(signals.due, now, horizon)
    return total


IMPORTANCE_WORDS = {-1: "not important", 1: "important", 2: "critical"}
PRIORITIES = {0: "Not important", 1: "Important", 2: "Critical"}
"""The priorities I can set, by level, as the buttons say them."""
PRIORITY_ICONS = {0: "❕", 1: "❗️", 2: "‼️"}
"""The visual icon beside each priority's unchanged label."""
PRIORITY_IMPORTANCE = {0: -1, 1: 1, 2: 2}
"""The `Signals.importance` each priority stands for."""


def why(
    signals: Signals,
    now: datetime | None = None,
    stale_after: timedelta | None = None,
    horizon: timedelta = DUE_HORIZON,
) -> str:
    """The signals that scored, in words: "critical, quick, closes APP-12, alice waiting 2d,
    due in 3d"."""
    parts: list[str] = []
    if signals.blocked:
        if now is not None and signals.touched and is_stale(signals, stale_after, now):
            parts.append(f"blocked, untouched {age(now, signals.touched)}")
        else:
            parts.append("blocked")
    if signals.importance in IMPORTANCE_WORDS:
        parts.append(IMPORTANCE_WORDS[signals.importance])
    if signals.effort == "minutes":
        parts.append("quick")
    elif signals.effort == "hour":
        parts.append("takes about an hour")
    if signals.closes:
        parts.append(f"closes {signals.closes}")
    if signals.waiting:
        waited = ""
        if now is not None and signals.waiting_since is not None:
            waited = f" {age(now, signals.waiting_since)}"
        parts.append(f"{', '.join(signals.waiting)} waiting{waited}")
    if now is not None and signals.due is not None and due_pressure(signals.due, now, horizon) > 0:
        if signals.due <= now:
            parts.append(f"overdue by {age(now, signals.due)}")
        else:
            parts.append(f"due in {age(signals.due, now)}")
    return ", ".join(parts)


@dataclass
class _Group:
    """One Act now item: its best nomination, what else is due on the same subject (by
    verb), and the items about a unit of work it's in that joined it, to do after it."""

    position: int
    """Its best member's place in the ranking."""
    nomination: Nomination
    also: list[Nomination]
    then: list[Nomination] = field(default_factory=list[Nomination])


def no_units(ref: Ref) -> list[Ref]:
    """No units of work: nothing joins (without a graph)."""
    return []


def _merged(kept: list[tuple[Nomination, list[Nomination]]], units: Units) -> list[_Group]:
    """`kept` (best first) with each item about a unit of work (its main subject a ticket
    or an entry) joined to the best item about something in that unit, which isn't one
    itself (a PR the ticket links): "Ping the reviewers, then move APP-12 to In Review". An
    item about a unit with nothing in it to join stays as it is, and two items about things
    in the same unit (two PRs of one ticket) stay apart. In each group's best member's
    order."""

    def subject(nomination: Nomination) -> Ref | None:
        return nomination.item.subjects[0] if nomination.item.subjects else None

    def is_unit(ref: Ref | None) -> bool:
        return ref is not None and ref in units(ref)

    groups: list[_Group] = []
    host_of: dict[Ref, _Group] = {}
    for position, (nomination, also) in enumerate(kept):
        ref = subject(nomination)
        if is_unit(ref):
            continue
        group = _Group(position, nomination, also)
        groups.append(group)
        for unit in units(ref) if ref else []:
            host_of.setdefault(unit, group)
    for position, (nomination, also) in enumerate(kept):
        ref = subject(nomination)
        if ref is None or not is_unit(ref):
            continue
        if (host := host_of.get(ref)) is None:
            groups.append(_Group(position, nomination, also))
        else:
            host.position = min(host.position, position)
            host.then.append(nomination)
            host.also += also
    return sorted(groups, key=lambda group: group.position)


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
    units: Units = no_units,
    priority_of: PriorityOf = no_priority,
    contact_importance: ContactImportance = no_contact_importance,
) -> list[Panel]:
    """Act now's panels: nominations not snoozed or dismissed (those I opened dimmed, in
    their place), pinned ones first and
    then the best (by score, then the order they came in), one
    per subject (the others mentioned on it), at most `per_plugin` from one plugin, `size`
    in all, each with buttons to snooze, dismiss, pin or prioritize it; who it's about can raise
    the item's importance, and a priority I set replaces it. They're split into a panel per
    plugin (keyed and, by `titles`, titled after it), so they flow across the page; the
    panels come in their best items' order, and each item is badged with its place overall.
    With nothing to act on, one empty panel says so."""
    config = config or ActNowConfig()
    feedback = feedback or {}
    size, per_plugin, weights = config.size, config.per_plugin, config.weights
    stale_after = timedelta(days=config.blocked_stale_days) or None
    horizon = timedelta(days=config.due_warning_days)
    levels: dict[int, int] = {}
    """The priority I set on a nomination, by its `id`."""
    prioritized_nominations: list[Nomination] = []
    for nomination in nominations:
        changed, level = prioritized(
            _with_contact_importance(nomination, contact_importance), priority_of
        )
        if level is not None:
            levels[id(changed)] = level
        prioritized_nominations.append(changed)
    nominations = prioritized_nominations

    def said(nomination: Nomination) -> Feedback | None:
        return feedback.get(nomination.key) if nomination.key else None

    def pinned(nomination: Nomination) -> bool:
        return (f := said(nomination)) is not None and f.action == "pin"

    candidates = [
        (n, nom)
        for n, nom in enumerate(nominations)
        if not (now and set_aside(nom.fingerprint, said(nom), now) is not None)
    ]
    candidates.sort(
        key=lambda c: (
            not pinned(c[1]),
            -score(c[1].signals, weights, now, stale_after, horizon),
            c[0],
        )
    )
    kept: dict[tuple[str, str] | Ref, tuple[Nomination, list[Nomination]]] = {}
    for n, nomination in candidates:
        subject = _subject(nomination, n)
        if subject in kept:
            kept[subject][1].append(nomination)
        else:
            kept[subject] = (nomination, [])
    per: dict[str, int] = {}
    by_plugin: dict[str, list[Item]] = {}
    place = 0
    for group in _merged(list(kept.values()), units):
        nomination = group.nomination
        if place == size:
            break
        if per.get(nomination.plugin, 0) == per_plugin:
            continue
        per[nomination.plugin] = per.get(nomination.plugin, 0) + 1
        is_pinned = any(pinned(n) for n in [nomination, *group.also, *group.then])
        shown = _shown(group, place, size, is_pinned, now, levels, stale_after, horizon)
        if now and (when := opened_at(nomination, said(nomination), now)):
            opened = Badge(text=f"opened {age(now, when)} ago")
            shown = shown.model_copy(update={"opened_at": when, "badges": [*shown.badges, opened]})
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


def feedback_fields(key: FeedbackKey, fingerprint: str) -> dict[str, str]:
    """The fields that name an item to what's said of it (`act_now/<action>`)."""
    kind, id, task = key
    return {"kind": kind, "id": id, "task": task, "fingerprint": fingerprint}


def _member_fields(
    nomination: Nomination, key: FeedbackKey, members_of: list[Nomination]
) -> dict[str, str]:
    """The fields that name an item (by its `key`), and those shown with it (`members`, posted
    as `also`), to the buttons that set it aside."""
    fields = feedback_fields(key, nomination.fingerprint)
    members = [[*n.key, n.fingerprint] for n in members_of if n.key is not None]
    if members:
        fields["also"] = json.dumps(members)
    return fields


def importance_actions(subject: Ref, level: int | None) -> list[Action]:
    """The three importance choices for a subject, with its current choice highlighted, and
    a way to clear it once there is one."""
    fields = {"kind": subject.kind, "id": subject.id}
    return [
        *(
            Action(
                name=f"{ACTIONS}/priority",
                label=label,
                fields={**fields, "level": str(choice)},
                group="Importance",
                selected=choice == level,
                icon=PRIORITY_ICONS[choice],
            )
            for choice, label in PRIORITIES.items()
        ),
        *(
            [Action(name=f"{ACTIONS}/priority", label="Clear priority", fields=fields)]
            if level is not None
            else []
        ),
    ]


def snooze_action(fields: dict[str, str], aside: SetAside | None = None) -> Action:
    """The Snooze button of an item named by `fields`, wherever it's shown; for one set aside
    (`aside`), Unsnooze or Undismiss in its place."""
    if aside is None:
        return Action(
            name=f"{ACTIONS}/snooze", label="Snooze", fields=fields, group="Act now", icon="💤"
        )
    return Action(
        name=f"{ACTIONS}/unsnooze",
        label="Unsnooze" if aside == "snooze" else "Undismiss",
        fields=fields,
        group="Act now",
        icon="⏰",
    )


def _buttons(
    nomination: Nomination, members: list[Nomination], pinned: bool, level: int | None
) -> list[Action]:
    """Snooze, dismiss, pin or unpin, and set the priority of its subject (or clear it); none
    for an item about nothing. Snooze, dismiss and pin act on those shown with it (`members`:
    what else is due on the subject, and the items joined to it) too, posted as `also`."""
    if nomination.key is None:
        return []
    fields = _member_fields(nomination, nomination.key, members)

    def button(action: str, label: str, icon: str, selected: bool | None = None) -> Action:
        return Action(
            name=f"{ACTIONS}/{action}",
            label=label,
            fields=fields,
            group="Act now",
            icon=icon,
            selected=selected,
        )

    return [
        snooze_action(fields),
        button("dismiss", "Dismiss", "👋"),
        button("unpin", "Unpin", "📌", True) if pinned else button("pin", "Pin", "📌", False),
        *importance_actions(nomination.item.subjects[0], level),
    ]


def _shown(
    group: _Group,
    place: int,
    size: int,
    pinned: bool,
    now: datetime | None,
    levels: dict[int, int],
    stale_after: timedelta | None,
    horizon: timedelta,
) -> Item:
    """The item as Act now shows it: its verb first, its place overall as a badge, why it's
    here, what else is due on the same subject and the items joined to it, with their buttons,
    the parts of it and of those, to act on one by one, and the buttons to set it aside. Ranked by its place, since the panels keep their own
    order."""
    nomination, then = group.nomination, group.then
    members = [*group.also, *then]
    item, signals = nomination.item, nomination.signals
    reason = ("pinned, " if pinned else "") + why(signals, now, stale_after, horizon)
    reason = reason.removesuffix(", ")
    details = [f"why: {reason}"] if reason else []
    details += item.details
    also = [n.signals.verb or n.item.title for n in group.also]
    details += [f"also: {', '.join(also)}"] if also else []
    details += [f"then: {_titled(joined)}" for joined in then]
    links = [*item.links]
    links += [
        Link(text=joined.item.title, url=joined.item.url) for joined in then if joined.item.url
    ]
    fields = (
        json.dumps(_member_fields(nomination, nomination.key, members)) if nomination.key else None
    )
    return item.model_copy(
        update={
            "act_now_fields": fields,
            "title": _titled(nomination),
            "details": details,
            "badges": [f"#{place + 1}", *item.badges],
            "links": links,
            "rank": size - place,
            "deferred": False,
            "parts": [
                *_listed_parts(item),
                # All of a joined item's parts, even one, so its buttons say what they act on.
                *(part for joined in then for part in joined.item.parts),
            ],
            "parts_listed": any(joined.item.parts for joined in then),
            "actions": [
                *_without_repeats(
                    [
                        *item.actions,
                        *(action for other in group.also for action in other.item.actions),
                        *(action for joined in then for action in _joined_actions(joined.item)),
                    ]
                ),
                *_buttons(nomination, members, pinned, levels.get(id(nomination))),
            ],
        }
    )


def _without_repeats(actions: list[Action]) -> list[Action]:
    """`actions` with each button once: two tasks on a subject can offer the same one."""
    seen: list[Action] = []
    for action in actions:
        if action not in seen:
            seen.append(action)
    return seen


def _titled(nomination: Nomination) -> str:
    """ "Verb: title", or the title of an item without a verb."""
    verb, title = nomination.signals.verb, nomination.item.title
    return f"{verb}: {title}" if verb else title
