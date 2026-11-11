"""The Dashboard: every enabled plugin's panels in groups, and focus mode's view of them.

Act now is core's group: the panels whose plugins flag them as needing acting on now. Every
other panel sits in its plugin's group. Panels and groups go in their bands' order (see
`Band`). Focus mode walks Act now, or one panel, an item at a time.
"""

import logging
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from pydantic import Field

from . import act_now
from .domain import Model
from .page import Action, Group, Identity, Item, ItemAdditions, Panel, SourceStatus, Surface

log = logging.getLogger(__name__)

_said_untasked: set[str] = set()
"""The plugins already logged for nominating an item without a task."""


def _tasked(plugin: str, item: Item) -> bool:
    """Whether a nominated item has a task (`Signals.task`), which Act now keeps what I say
    of it by. One without is a plugin bug: it's left out of Act now, logged once a plugin."""
    if item.signals and item.signals.task:
        return True
    if plugin not in _said_untasked:
        _said_untasked.add(plugin)
        log.error("%s nominated %r for Act now without a task; it's left out", plugin, item.title)
    return False


ACT_NOW = "act"
"""Act now's group key."""
ACT_NOW_TITLE = "Act now"


@dataclass(frozen=True)
class PluginPanels:
    """One enabled plugin's panels, which make its group on the page."""

    key: str
    title: str
    panels: list[Panel]
    nominations: list[Item] = field(default_factory=list[Item])
    """Its candidates for Act now (see `Plugin.act_now`)."""


def panel_key(plugin: str, key: str) -> str:
    """A panel's key on the page: its plugin's name, then the key the plugin gave it."""
    return f"{plugin}.{key}"


def _by_priority(panels: list[Panel]) -> list[str]:
    """The panels' keys by band, those in one band in the order given (`sorted` is stable)."""
    return [p.key for p in sorted(panels, key=lambda p: p.priority)]


def groups(plugins: list[PluginPanels], act_now_keys: list[str]) -> list[Group]:
    """Act now with its `act_now_keys` panels, then a group per plugin: panels by band, in the
    order the plugin gives those in one, and groups by their most urgent panel's band, then by
    name."""
    result = [Group(key=ACT_NOW, title=ACT_NOW_TITLE, panel_keys=act_now_keys, open=True)]
    rest = sorted(
        [plugin for plugin in plugins if plugin.panels],
        key=lambda plugin: (min(p.priority for p in plugin.panels), plugin.key),
    )
    for plugin in rest:
        result.append(
            Group(key=plugin.key, title=plugin.title, panel_keys=_by_priority(plugin.panels))
        )
    return result


MAX_LAYOUT_KEYS = 500
"""More groups or panels than any page has: a bound on what `POST /layout` stores."""


class Layout(Model):
    """The page's order, as someone dragged it: groups in order, and each group's panels."""

    groups: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list[str], max_length=MAX_LAYOUT_KEYS
    )
    panels: dict[str, list[Annotated[str, Field(max_length=200)]]] = Field(
        default_factory=dict[str, list[str]], max_length=MAX_LAYOUT_KEYS
    )


def _arranged(default: list[str], stored: list[str]) -> list[str]:
    """`default` in the `stored` order. One `stored` doesn't mention (a new plugin or panel)
    stays right after the one before it in `default`, or first if it was first."""
    position = {key: n for n, key in enumerate(stored)}
    keyed: list[tuple[float, int, str]] = []
    last = -1.0
    for n, key in enumerate(default):
        last = position[key] if key in position else last + 0.001
        keyed.append((last, n, key))
    return [key for _, _, key in sorted(keyed)]


def laid_out(groups: list[Group], layout: Layout) -> list[Group]:
    """`groups` in the layout's order, Act now first whatever it says, each with its panels
    in the layout's order."""
    act_now = [g for g in groups if g.key == ACT_NOW]
    rest = {g.key: g for g in groups if g.key != ACT_NOW}
    ordered = [rest[key] for key in _arranged(list(rest), layout.groups)]
    return [
        g.model_copy(update={"panel_keys": _arranged(g.panel_keys, layout.panels.get(g.key, []))})
        for g in act_now + ordered
    ]


class Dashboard(Model):
    generated_at: datetime
    identities: list[Identity]
    """Who I am in each tool that says, in plugin order."""
    panels: list[Panel]
    groups: list[Group]
    sources: dict[str, SourceStatus]
    problems: list[str] = Field(default_factory=list[str])
    """What's wrong with the installed plugins: those left out, and why."""
    plugin_problems: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    """What each enabled plugin reports wrong besides a failed refresh (`Context.problem`), by
    plugin; a plugin without any isn't there."""

    def panel(self, key: str) -> Panel:
        return next(p for p in self.panels if p.key == key)

    def find_panel(self, key: str) -> Panel | None:
        return next((p for p in self.panels if p.key == key), None)

    def find_group(self, key: str) -> Group | None:
        return next((g for g in self.groups if g.key == key), None)

    def focus_title(self, category: str) -> str | None:
        """What the page calls a focus category; None for an unknown one.

        A category is Act now (`ACT_NOW`) or a panel key.
        """
        if category == ACT_NOW:  # its group is left out while the tag view leaves it empty
            group = self.find_group(ACT_NOW)
            return group.title if group else ACT_NOW_TITLE
        if (panel := self.find_panel(category)) is not None:
            return panel.title
        return None

    def _act_now(self) -> list[tuple[Panel, Item]]:
        group = self.find_group(ACT_NOW)
        keys = group.panel_keys if group else []
        found = [(p, item) for key in keys if (p := self.find_panel(key)) for item in p.items]
        return sorted(found, key=lambda pair: -pair[1].rank)

    def act_now(self) -> list[Item]:
        """Act now's items in their order across every tool, whichever panel each is in."""
        return [item for _, item in self._act_now()]

    def focus_items(self, category: str) -> list[tuple[Panel, Item]] | None:
        """The items focus mode steps through for a category, each with its panel.

        Act now's come in its own order across the tools. A panel's come in its order, and
        an item with `parts` (an entry's todos) contributes those instead of itself; what's set
        aside or done isn't a step. None for an unknown category.
        """
        if category == ACT_NOW:
            return [(panel, item) for panel, item in self._act_now()]
        if (panel := self.find_panel(category)) is None:
            return None
        return [
            (panel, part)
            for item in panel.items
            if not item.set_aside and not item.done  # nothing to do now
            for part in item.parts or [item]
            if not part.done
        ]

    def focus_step(self, category: str, index: int) -> tuple[Panel, Item] | None:
        """The category's step focus mode shows at `index`: the last for an index past the end
        (the list shrank since the link was made); None when it has none."""
        steps = self.focus_items(category) or []
        return steps[max(0, min(index, len(steps) - 1))] if steps else None

    def kept(
        self, category: str, key: str, remembered: tuple[str, Item] | None = None
    ) -> "int | Kept | None":
        """Where focus mode finds again the item a page of the category showed (`key`, its
        `focus_key`), once a button there was pressed: its index, while it's still a step there,
        wherever the button moved it. Once it has left the category (a todo ticked off, an item
        snoozed), what to show in its place, so what the button did can be seen and undone:
        the item as it is now, wherever the page still shows it (the todo with its box ticked,
        the item with Unsnooze), with that panel's key; else, when the page's copy is
        `remembered` (with its panel's key), that copy, gone, without the buttons that post,
        as what they'd act on is gone. None when it's nowhere and there's no copy."""
        steps = self.focus_items(category) or []
        for n, (_, step) in enumerate(steps):
            if focus_key(step) == key:
                return n
        own = remembered[0] if remembered else category
        for panel in sorted(self.panels, key=lambda p: p.key != own):  # its own panel first
            if (now := _found(panel.items, key)) is not None:
                return Kept(panel.key, now)
        if remembered is None:
            return None
        panel, item = remembered
        posting = {"actions": [a for a in item.actions if a.href is not None], "parts": []}
        return Kept(panel, item.model_copy(update=posting), gone=True)


@dataclass(frozen=True)
class Kept:
    """An item focus mode shows in place of a step, after a button took it out of the
    category (`Dashboard.kept`)."""

    panel: str
    """The key of the panel it's in, or was in when it's `gone`."""
    item: Item
    gone: bool = False
    """It's on the page no more (deleted, dismissed): this is the copy the page showed."""


def focus_key(item: Item) -> str:
    """What focus mode finds an item again by, once a button was pressed on it
    (`Dashboard.kept`): its anchor, from its main subject; or, about nothing, `title:` and
    its title."""
    return item.anchor or f"title:{item.title}"


def _found(items: list[Item], key: str) -> Item | None:
    """The item among `items`, or their parts, whose `focus_key` is `key`."""
    for item in items:
        if focus_key(item) == key:
            return item
        if (part := _found(item.parts, key)) is not None:
            return part
    return None


def _sorted(
    panel: Panel,
    priority_of: act_now.PriorityOf = act_now.no_priority,
    contact_importance: act_now.ContactImportance = act_now.no_contact_importance,
) -> Panel:
    """Highest rank first, deferred items after, and what's set aside or done last; optionally
    importance before rank."""

    def importance(item: Item) -> int:
        assigned = [priority_of(ref) for ref in item.subjects]
        levels = [level for level in assigned if level is not None]
        own = [item.signals.importance if item.signals else 0]
        own += [contact_importance(ref) for ref in item.subjects]
        return max(levels, default=max(own))

    return panel.model_copy(
        update={
            "items": sorted(
                panel.items,
                key=lambda i: (
                    i.set_aside or i.done,
                    i.deferred,
                    -(importance(i) if panel.sort_by_importance else 0),
                    -i.rank,
                ),
            )
        }
    )


ItemActions = Callable[[Item, str, Surface], ItemAdditions]
"""What the plugins add to an item, whoever built it (the second argument, its plugin), on a
surface; see `Plugin.item_actions`."""


def _no_item_actions(item: Item, owner: str, surface: Surface) -> ItemAdditions:
    return ItemAdditions()


TagsOf = Callable[[Item], list[str]]
"""The tags whose badges core shows on an item (see `tags.Tags.of`)."""


def _no_tags(item: Item) -> list[str]:
    return []


def _shown_tags(item: Item) -> list[str]:
    return item.tags


def carries(item: Item, tags: AbstractSet[str], tags_of: TagsOf = _shown_tags) -> bool:
    """Whether `item`, or one of its parts, shows the badge of one of `tags`: those its `tags`
    say, once built, or `tags_of` says, before."""
    return any(t in tags for t in tags_of(item)) or any(
        carries(part, tags, tags_of) for part in item.parts
    )


Hidden = Callable[[Item], bool]
"""Whether an item is about something hidden, e.g. on ice, and so left off the page."""


def _nothing_hidden(item: Item) -> bool:
    return False


def _without_hidden(panel: Panel, hidden: Hidden) -> Panel:
    return panel.model_copy(update={"items": [i for i in panel.items if not hidden(i)]})


def _as_nomination(plugin: str, panel: Panel, item: Item) -> act_now.Nomination:
    """A panel's item as Act now would know it: with the panel's signals unless it has its
    own."""
    return act_now.Nomination(
        plugin, item if item.signals else item.model_copy(update={"signals": panel.signals})
    )


def fingerprints(plugins: list[PluginPanels]) -> dict[act_now.FeedbackKey, str]:
    """The fingerprint of every item the plugins show or nominate, by its key, as the page
    compares a snooze with it: Act now's nomination's, else its panel item's."""
    found = {
        n.key: n.fingerprint
        for plugin in plugins
        for panel in plugin.panels
        for item in panel.items
        if (n := _as_nomination(plugin.key, panel, item)).key is not None
    }
    found.update(
        (n.key, n.fingerprint)
        for plugin in plugins
        for item in plugin.nominations
        if (n := act_now.Nomination(plugin.key, item)).key is not None
    )
    return found


def _with_set_aside(
    panel: Panel,
    plugin: str,
    feedback: dict[act_now.FeedbackKey, act_now.Feedback],
    now: datetime,
    nominated: dict[act_now.FeedbackKey, str],
) -> Panel:
    """Each of a panel's items that has a task with Snooze, as in Act now; one snoozed there or
    here (`act_now.set_aside`) marked so, to show dimmed and last, with Unsnooze. An item is
    known by the fingerprint Act now has for it (`nominated`, by key), so both agree, else its
    own; its parts without a task of their own carry its buttons, as focus mode shows them,
    and they set it aside, as a part's Put on ice does its entry."""

    def with_buttons(item: Item) -> Item:
        nomination = _as_nomination(plugin, panel, item)
        key = nomination.key
        if key is None:
            return item
        fingerprint = nominated.get(key, nomination.fingerprint)
        aside = act_now.set_aside(fingerprint, feedback.get(key), now)
        buttons = act_now.set_aside_actions(act_now.feedback_fields(key, fingerprint), aside)
        parts = [
            part
            if act_now.Nomination(plugin, part).key is not None
            else part.model_copy(update={"actions": [*part.actions, *buttons], "set_aside": aside})
            for part in item.parts
        ]
        return item.model_copy(
            update={"actions": [*item.actions, *buttons], "set_aside": aside, "parts": parts}
        )

    return panel.model_copy(update={"items": [with_buttons(item) for item in panel.items]})


KnownAction = Callable[[str], bool]
"""Whether a button's name, `<plugin>/<action>`, is an action an enabled plugin declares."""


def _any_action(name: str) -> bool:
    return True


_said_unknown: set[str] = set()
"""The button names already logged as naming no action."""


def _known(action: Action, known: KnownAction) -> bool:
    """Whether a button posts to an action that's there: Act now's own, a link, or one an
    enabled plugin declares. One that isn't is a plugin bug (a typo, a plugin not enabled):
    it's left out, so it can't post to a 404, and logged once."""
    if action.href is not None or action.name.startswith(f"{act_now.ACTIONS}/"):
        return True
    if known(action.name):
        return True
    if action.name not in _said_unknown:
        _said_unknown.add(action.name)
        log.error(
            "a button posts to %r, which no enabled plugin declares; it's left out", action.name
        )
    return False


def _with_item_actions(
    panel: Panel,
    owner: str,
    surface: Surface,
    item_actions: ItemActions,
    known: KnownAction = _any_action,
    tags_of: TagsOf = _no_tags,
) -> Panel:
    """Each item, and each of its focus-mode parts, with what the plugins add: buttons,
    badges, details and links, and the tags core's badges show (`tags_of`). `owner` is the
    plugin whose panel it is. Buttons posting to no action `known` knows are left out. Nothing
    is added to a part that's `done` (a todo ticked off, kept to untick): it's finished work
    within its item, with nothing to tag, ice or reach anyone about, so only its own buttons
    act on it. An item that's done itself (an entry whose todos are all ticked off) is still
    there to tag, ice and so on, and gets what any item does."""

    def add(item: Item, part: bool = False) -> Item:
        added = ItemAdditions() if part and item.done else item_actions(item, owner, surface)
        actions = [a for a in [*item.actions, *added.actions] if _known(a, known)]
        return item.model_copy(
            update={
                "actions": actions,
                "tags": tags_of(item),
                "badges": [*item.badges, *added.badges],
                "details": [*item.details, *added.details],
                "links": [*item.links, *added.links],
                "parts": [add(each, part=True) for each in item.parts],
            }
        )

    return panel.model_copy(update={"items": [add(item) for item in panel.items]})


def _as_given(item: Item) -> Item:
    """A plugin's item without what only core sets (`act_now_fields`, `opened_at`,
    `parts_listed`, `set_aside`). Its `tags` are set over by `_with_item_actions`, on every
    item shown."""
    if (
        item.act_now_fields is None
        and item.opened_at is None
        and not item.set_aside
        and not item.parts
    ):
        return item
    return item.model_copy(
        update={
            "act_now_fields": None,
            "opened_at": None,
            "set_aside": False,
            "parts_listed": False,
            "parts": [_as_given(part) for part in item.parts],
        }
    )


HidesKind = Callable[[str], bool]
"""Whether a kind's entities hide what they point at (see `Graph.hides`)."""


def _hides_nothing(kind: str) -> bool:
    return False


def _with_importance_actions(
    panel: Panel, priority_of: act_now.PriorityOf, hides: HidesKind, parts_only: bool = False
) -> Panel:
    """Add core's importance choices to every shown entity, including focus parts. An
    item's first subject is its primary entity, as for its anchor and Act now. An item about
    what hides things (ice) has none: it's parked, so nothing ranks it; nor has a part that's
    `done` (a todo ticked off), finished within its item (see `_with_item_actions`). With
    `parts_only`, only to the parts: Act now gives its items their own."""

    def add(item: Item, top: bool) -> Item:
        actions = list(item.actions)
        finished = item.done and not top
        ranked = item.subjects and not finished and not hides(item.subjects[0].kind)
        if not (top and parts_only) and ranked:
            subject = item.subjects[0]
            actions.extend(act_now.importance_actions(subject, priority_of(subject)))
        return item.model_copy(
            update={"actions": actions, "parts": [add(part, False) for part in item.parts]}
        )

    return panel.model_copy(update={"items": [add(item, True) for item in panel.items]})


def _opened_on_buttons(panel: Panel) -> Panel:
    """Act now's items with their plugins' buttons (not Act now's own) carrying the item's
    `act_now_fields`, so that running one marks the item opened, dimmed until it changes."""

    def tag(item: Item) -> Item:
        if item.act_now_fields is None:
            return item
        actions = [
            a
            if a.href is not None or a.name.startswith(f"{act_now.ACTIONS}/")
            else a.model_copy(
                update={"fields": {**a.fields, act_now.OPENED_FIELD: item.act_now_fields}}
            )
            for a in item.actions
        ]
        return item.model_copy(update={"actions": actions})

    return panel.model_copy(update={"items": [tag(item) for item in panel.items]})


def build(
    now: datetime,
    plugins: list[PluginPanels],
    sources: dict[str, SourceStatus],
    identities: list[Identity] | None = None,
    item_actions: ItemActions = _no_item_actions,
    hidden: Hidden = _nothing_hidden,
    layout: Layout | None = None,
    ranking: act_now.ActNowConfig | None = None,
    feedback: dict[act_now.FeedbackKey, act_now.Feedback] | None = None,
    units: act_now.Units = act_now.no_units,
    priority_of: act_now.PriorityOf = act_now.no_priority,
    surface: Surface = "dashboard",
    known_action: KnownAction = _any_action,
    hides: HidesKind = _hides_nothing,
    contact_importance: act_now.ContactImportance = act_now.no_contact_importance,
    tags_of: TagsOf = _no_tags,
    selected: Sequence[str] = (),
) -> Dashboard:
    """The dashboard: Act now, ranked from the plugins' nominations, then the plugins' panels in
    their groups, without hidden items, each item with the plugins' buttons, sorted, in the
    `layout`'s order. `ranking` is `[act_now]`, `feedback` what I said of its items, and
    `units` the graph's, for Act now to join items about the same unit of work, and
    `priority_of` what I set as the priority of what an item is about; `known_action` says
    which buttons post to an action that's there, `hides` which kinds hide what they point
    at, `contact_importance` what who an item is about adds to it, and `tags_of` the tags
    core's badges show on an item, kept in its `tags`. With tags `selected` (the tag view, see
    `tag_view`), Act now shows only its items with a member carrying one, so it shows the top
    of those, not those of its top that carry one, each with all its members. A panel's key is
    its plugin's name and its own, e.g. `github.conflicts`, so two plugins' panels never
    clash."""
    plugins = [
        PluginPanels(
            plugin.key,
            plugin.title,
            [
                p.model_copy(
                    update={
                        "key": panel_key(plugin.key, p.key),
                        "items": [_as_given(i) for i in p.items],
                    }
                )
                for p in plugin.panels
            ],
            [_as_given(i) for i in plugin.nominations],
        )
        for plugin in plugins
    ]
    nominations = [
        act_now.Nomination(plugin.key, item)
        for plugin in plugins
        for item in plugin.nominations
        if not hidden(item) and _tasked(plugin.key, item)
    ]
    wanted = set(selected)

    def shows(item: Item) -> bool:
        return not wanted or carries(item, wanted, tags_of)

    nominated = fingerprints(plugins)
    titles = {plugin.key: plugin.title for plugin in plugins}
    # Act now has a panel per plugin, keyed by the plugin's name: that's whose its items are.
    # Its listed parts get the importance choices they have in their panels, as the same
    # todo offers the same wherever it's shown.
    ranked = [
        _with_importance_actions(
            _with_item_actions(p, p.key, "act_now", item_actions, known_action, tags_of),
            priority_of,
            hides,
            parts_only=True,
        ).model_copy(update={"key": panel_key(ACT_NOW, p.key)})
        for p in act_now.panels(
            nominations,
            ranking,
            feedback,
            now,
            titles,
            units,
            priority_of,
            contact_importance,
            shows,
        )
    ]
    return Dashboard(
        generated_at=now,
        identities=identities or [],
        panels=[
            *(_opened_on_buttons(panel) for panel in ranked),
            *(
                _sorted(
                    _with_importance_actions(
                        _with_item_actions(
                            _without_hidden(shown, hidden),
                            plugin.key,
                            surface,
                            item_actions,
                            known_action,
                            tags_of,
                        ),
                        priority_of,
                        hides,
                    ),
                    priority_of,
                    contact_importance,
                )
                for plugin in plugins
                for panel in plugin.panels
                for shown in [_with_set_aside(panel, plugin.key, feedback or {}, now, nominated)]
            ),
        ],
        groups=laid_out(groups(plugins, [p.key for p in ranked]), layout or Layout()),
        sources=sources,
    )
