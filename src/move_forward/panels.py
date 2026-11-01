"""The Dashboard: every enabled plugin's panels in groups, and focus mode's view of them.

Act now is core's group: the panels whose plugins flag them as needing acting on now. Every
other panel sits in its plugin's group. Panels and groups go in priority order (the skill's
step order); focus mode goes through them as the page shows them.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

from pydantic import Field

from . import act_now
from .domain import Model
from .page import Action, Group, Item, Panel, SourceStatus

ACT_NOW = "act"
"""Act now's group key."""


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
    return [p.key for p in sorted(panels, key=lambda p: p.priority)]


ACT_NOW_PANEL = f"{ACT_NOW}.{act_now.KEY}"
"""The Act now panel's key: the only panel in core's Act now group."""


def groups(plugins: list[PluginPanels]) -> list[Group]:
    """Act now, then a group per plugin: panels by priority, and groups by their most urgent
    panel's."""
    result = [Group(key=ACT_NOW, title="Act now", panel_keys=[ACT_NOW_PANEL], open=True)]
    rest = sorted(
        [plugin for plugin in plugins if plugin.panels],
        key=lambda plugin: min(p.priority for p in plugin.panels),
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


FOCUS_ALL = "all"
"""The focus category that covers every panel."""


class Dashboard(Model):
    generated_at: datetime
    login: str
    panels: list[Panel]
    groups: list[Group]

    def panel(self, key: str) -> Panel:
        return next(p for p in self.panels if p.key == key)

    def find_panel(self, key: str) -> Panel | None:
        return next((p for p in self.panels if p.key == key), None)

    def find_group(self, key: str) -> Group | None:
        return next((g for g in self.groups if g.key == key), None)

    def focus_title(self, category: str) -> str | None:
        """What the page calls a focus category; None for an unknown one.

        A category is `FOCUS_ALL`, a group key or a panel key.
        """
        if category == FOCUS_ALL:
            return "All"
        if (group := self.find_group(category)) is not None:
            return group.title
        if (panel := self.find_panel(category)) is not None:
            return panel.title
        return None

    def focus_items(self, category: str) -> list[tuple[Panel, Item]] | None:
        """The items focus mode steps through for a category, each with its panel.

        Panels come as the page shows them (All leaves out Act now, whose items are in the
        plugins' panels too), items in their panel's order, and an item with `parts`
        contributes those instead of itself; None for an unknown category.
        """
        if category == FOCUS_ALL:  # Act now's items are all in some plugin's panel too
            keys = [key for g in self.groups if g.key != ACT_NOW for key in g.panel_keys]
        elif (group := self.find_group(category)) is not None:
            keys = group.panel_keys
        elif self.find_panel(category) is not None:
            keys = [category]
        else:
            return None
        panels = [p for key in keys if (p := self.find_panel(key))]
        return [
            (panel, part)
            for panel in panels
            for item in panel.items
            for part in item.parts or [item]
        ]

    sources: dict[str, SourceStatus]


def _sorted(panel: Panel) -> Panel:
    """Highest rank first, drafts last."""
    return panel.model_copy(update={"items": sorted(panel.items, key=lambda i: (i.draft, -i.rank))})


ItemActions = Callable[[Item], list[Action]]
"""The buttons the plugins add to an item, whoever built it; see `Plugin.item_actions`."""


def _no_item_actions(item: Item) -> list[Action]:
    return []


Hidden = Callable[[Item], bool]
"""Whether an item is about something hidden, e.g. on ice, and so left off the page."""


def _nothing_hidden(item: Item) -> bool:
    return False


def _without_hidden(panel: Panel, hidden: Hidden) -> Panel:
    return panel.model_copy(update={"items": [i for i in panel.items if not hidden(i)]})


def _with_item_actions(panel: Panel, item_actions: ItemActions) -> Panel:
    """Each item, and each of its focus-mode parts, with the plugins' buttons added."""

    def add(item: Item) -> Item:
        return item.model_copy(
            update={
                "actions": [*item.actions, *item_actions(item)],
                "parts": [add(part) for part in item.parts],
            }
        )

    return panel.model_copy(update={"items": [add(item) for item in panel.items]})


def build(
    now: datetime,
    plugins: list[PluginPanels],
    sources: dict[str, SourceStatus],
    login: str = "",
    item_actions: ItemActions = _no_item_actions,
    hidden: Hidden = _nothing_hidden,
    layout: Layout | None = None,
) -> Dashboard:
    """The dashboard: Act now, ranked from the plugins' nominations, then the plugins' panels in
    their groups, without hidden items, each item with the plugins' buttons, sorted, in the
    `layout`'s order. A panel's key is its plugin's name and its own, e.g. `github.conflicts`,
    so two plugins' panels never clash."""
    plugins = [
        PluginPanels(
            plugin.key,
            plugin.title,
            [p.model_copy(update={"key": panel_key(plugin.key, p.key)}) for p in plugin.panels],
            plugin.nominations,
        )
        for plugin in plugins
    ]
    nominations = [
        act_now.Nomination(plugin.key, item)
        for plugin in plugins
        for item in plugin.nominations
        if not hidden(item)
    ]
    ranked = act_now.panel(nominations).model_copy(update={"key": ACT_NOW_PANEL})
    return Dashboard(
        generated_at=now,
        login=login,
        panels=[
            _with_item_actions(ranked, item_actions),
            *(
                _sorted(_with_item_actions(_without_hidden(panel, hidden), item_actions))
                for plugin in plugins
                for panel in plugin.panels
            ),
        ],
        groups=laid_out(groups(plugins), layout or Layout()),
        sources=sources,
    )
