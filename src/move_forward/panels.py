"""The Dashboard: every enabled plugin's panels in groups, and focus mode's view of them.

Act now is core's group: the panels whose plugins flag them as needing acting on now. Every
other panel sits in its plugin's group. Panels and groups go in priority order (the skill's
step order); focus mode goes through them as the page shows them.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

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


def _by_priority(panels: list[Panel]) -> list[str]:
    return [p.key for p in sorted(panels, key=lambda p: p.priority)]


def groups(plugins: list[PluginPanels]) -> list[Group]:
    """Act now, then a group per plugin with panels outside Act now: panels by priority, and
    groups by their most urgent panel's."""
    act_now = [p for plugin in plugins for p in plugin.panels if p.act_now]
    result = [Group(key=ACT_NOW, title="Act now", panel_keys=_by_priority(act_now), open=True)]
    rest = [(plugin, [p for p in plugin.panels if not p.act_now]) for plugin in plugins]
    rest = sorted([(pl, ps) for pl, ps in rest if ps], key=lambda r: min(p.priority for p in r[1]))
    for plugin, panels in rest:
        result.append(Group(key=plugin.key, title=plugin.title, panel_keys=_by_priority(panels)))
    return result


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

        Panels come as the page shows them, items in their panel's order, and an item with
        `parts` contributes those instead of itself; None for an unknown category.
        """
        if category == FOCUS_ALL:
            keys = [key for group in self.groups for key in group.panel_keys]
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
) -> Dashboard:
    """The dashboard: the plugins' panels in their groups, without hidden items, each item with
    the plugins' buttons, sorted."""
    return Dashboard(
        generated_at=now,
        login=login,
        panels=[
            _sorted(_with_item_actions(_without_hidden(panel, hidden), item_actions))
            for plugin in plugins
            for panel in plugin.panels
        ],
        groups=groups(plugins),
        sources=sources,
    )
