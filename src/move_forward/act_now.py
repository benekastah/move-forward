"""Act now: core's short, ranked list of the best next actions, whatever the tools.

Each plugin nominates items through its `act_now` hook, each with `Signals` saying how long
it takes, what it finishes and who's waiting. Core drops what's blocked, scores the rest,
keeps one item per subject and a few per plugin, and says on each item why it's there.
"""

from dataclasses import dataclass

from .graph import Ref
from .page import Item, Panel, Signals

KEY = "now"
"""The Act now panel's key within core's Act now group."""
SIZE = 7
"""How many items the panel shows."""
PER_PLUGIN = 3
"""How many of them one plugin may fill, so one busy tool can't take the whole list."""
WEIGHTS = {"closes": 3.0, "minutes": 2.0, "hour": 1.0, "waiting": 2.0}
"""What each signal adds to an item's score."""


@dataclass(frozen=True)
class Nomination:
    """An item a plugin put forward for Act now."""

    plugin: str
    item: Item

    @property
    def signals(self) -> Signals:
        return self.item.signals or Signals()


def score(signals: Signals, weights: dict[str, float]) -> float:
    """Finishing beats starting, quick beats slow, and someone waiting adds urgency. Unknown
    effort adds nothing, so an item of unknown size never takes a quick win's place."""
    return (
        weights.get("closes", 0) * (signals.closes is not None)
        + weights.get("minutes", 0) * (signals.effort == "minutes")
        + weights.get("hour", 0) * (signals.effort == "hour")
        + weights.get("waiting", 0) * bool(signals.waiting)
    )


def why(signals: Signals) -> str:
    """The signals that scored, in words: "quick, closes APP-12, alice waiting"."""
    parts: list[str] = []
    if signals.effort == "minutes":
        parts.append("quick")
    elif signals.effort == "hour":
        parts.append("about an hour")
    if signals.closes:
        parts.append(f"closes {signals.closes}")
    if signals.waiting:
        parts.append(f"{', '.join(signals.waiting)} waiting")
    return ", ".join(parts)


def _subject(nomination: Nomination, n: int) -> tuple[str, str] | Ref:
    """What the item is about, for one item per subject; an item about nothing is its own."""
    subjects = nomination.item.subjects
    return subjects[0] if subjects else (nomination.plugin, str(n))


def panel(
    nominations: list[Nomination],
    size: int = SIZE,
    per_plugin: int = PER_PLUGIN,
    weights: dict[str, float] = WEIGHTS,
) -> Panel:
    """The Act now panel: nominations not blocked, best first (by score, then the plugin's
    own rank, then the order they came in), one per subject (the others mentioned on it),
    at most `per_plugin` from one plugin, `size` in all."""
    candidates = [(n, nom) for n, nom in enumerate(nominations) if not nom.signals.blocked]
    candidates.sort(key=lambda c: (-score(c[1].signals, weights), -c[1].item.rank, c[0]))
    kept: dict[tuple[str, str] | Ref, tuple[Nomination, list[str]]] = {}
    for n, nomination in candidates:
        subject = _subject(nomination, n)
        if subject in kept:
            kept[subject][1].append(nomination.signals.verb or nomination.item.title)
        else:
            kept[subject] = (nomination, [])
    per: dict[str, int] = {}
    items: list[Item] = []
    for nomination, also in kept.values():
        if len(items) == size:
            break
        if per.get(nomination.plugin, 0) == per_plugin:
            continue
        per[nomination.plugin] = per.get(nomination.plugin, 0) + 1
        items.append(_shown(nomination, also, len(items), size))
    return Panel(
        key=KEY,
        title="Act now",
        description="The best next actions across every tool, and why each is here.",
        items=items,
    )


def _shown(nomination: Nomination, also: list[str], place: int, size: int) -> Item:
    """The item as Act now shows it: its verb first, why it's here, and what else is due on
    the same subject. Ranked by its place, since the panel keeps its own order."""
    item, signals = nomination.item, nomination.signals
    reason = why(signals)
    details = [f"why: {reason}"] if reason else []
    details += item.details
    details += [f"also: {', '.join(also)}"] if also else []
    return item.model_copy(
        update={
            "title": f"{signals.verb}: {item.title}" if signals.verb else item.title,
            "details": details,
            "rank": size - place,
            "draft": False,
            "parts": [],
        }
    )
