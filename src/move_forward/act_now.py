"""Act now: core's short, ranked list of the best next actions, whatever the tools.

Each plugin nominates items through its `act_now` hook, each with `Signals` saying how long
it takes, what it finishes and who's waiting. Core drops what's blocked, scores the rest,
keeps one item per subject and a few per plugin, and says on each item why it's there.
"""

from dataclasses import dataclass

from pydantic import Field

from .graph import Ref
from .page import Item, Panel, Signals
from .strict import Strict

KEY = "now"
"""The Act now panel's key within core's Act now group."""


class Weights(Strict):
    """What each signal adds to an item's score."""

    closes: float = 3
    """Doing it finishes something: a PR merged, a ticket closed."""
    minutes: float = 2
    """It takes minutes."""
    hour: float = 1
    """It takes about an hour."""
    waiting: float = 2
    """Someone is waiting on me for it."""


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


def score(signals: Signals, weights: Weights) -> float:
    """Finishing beats starting, quick beats slow, and someone waiting adds urgency. Unknown
    effort adds nothing, so an item of unknown size never takes a quick win's place."""
    return (
        weights.closes * (signals.closes is not None)
        + weights.minutes * (signals.effort == "minutes")
        + weights.hour * (signals.effort == "hour")
        + weights.waiting * bool(signals.waiting)
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


def panel(nominations: list[Nomination], config: ActNowConfig | None = None) -> Panel:
    """The Act now panel: nominations not blocked, best first (by score, then the plugin's
    own rank, then the order they came in), one per subject (the others mentioned on it),
    at most `per_plugin` from one plugin, `size` in all."""
    config = config or ActNowConfig()
    size, per_plugin, weights = config.size, config.per_plugin, config.weights
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
