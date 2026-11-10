"""The tag view: the page shows only what carries one of the tags selected in its address,
`?tag=chores&tag=garbage-day` (any of them, not all).

An item carries a tag when it shows the tag's `#tag` badge (`Item.tags`, set from the badges'
own tags), and an item with parts (an entry's todos) when it or one of them does: it's kept
whole, parts and all, in its panel. Focus mode, which steps through a panel's parts one at a
time, skips those carrying none (unless their item does). Act now is filtered as it's built
(`panels.build`'s `selected`), so it shows the top of what carries one, each item with all its
members (a PR's with its ticket's move), as one member carrying it is enough. A panel or a
group left empty is left off the page. The selection is kept in every link and form that comes
back to the page (see `render`), so it survives the page's reload, focus mode and a button's
redirect."""

from collections.abc import Mapping, Sequence
from urllib.parse import urlencode

from .graph import Ref
from .page import Item
from .panels import ACT_NOW, Dashboard, carries
from .tags import handle

PARAM = "tag"
"""The query parameter naming a selected tag, once per tag."""


def selected(params: Mapping[str, list[str]]) -> list[str]:
    """The tags a query selects, by handle (`#Garbage Day` is `garbage-day`), once each, in the
    order given; a value naming no tag (`?tag=` from the drop-down's prompt, `?tag=!!`) selects
    nothing."""
    return list(dict.fromkeys(found for value in params.get(PARAM, []) if (found := handle(value))))


def url(path: str, tags: Sequence[str], params: Mapping[str, str] | None = None) -> str:
    """`path` with `params`, then `tags` as the selection."""
    pairs = [*(params or {}).items(), *((PARAM, tag) for tag in tags)]
    return path + ("?" + urlencode(pairs) if pairs else "")


def adding(tags: Sequence[str], tag: str) -> list[str]:
    """The selection `tags` with `tag` too, at its end, once."""
    return list(dict.fromkeys([*tags, tag]))


def without(tags: Sequence[str], tag: str) -> list[str]:
    """The selection `tags` without `tag`."""
    return [t for t in tags if t != tag]


def filtered(dashboard: Dashboard, tags: Sequence[str]) -> Dashboard:
    """The dashboard, built with `tags` selected, with only the items carrying one of them in
    its plugins' panels, every one without any; Act now's are left as built, filtered already.
    A panel left empty is kept, for focus mode to say so, but leaves its group's `panel_keys`,
    and a group left with none leaves the page. No `tags`, no filter."""
    if not tags:
        return dashboard
    wanted = set(tags)
    act_now = dashboard.find_group(ACT_NOW)
    built = set(act_now.panel_keys) if act_now else set[str]()
    panels = [
        panel
        if panel.key in built
        else panel.model_copy(update={"items": [i for i in panel.items if carries(i, wanted)]})
        for panel in dashboard.panels
    ]
    shown = {panel.key for panel in panels if panel.items}
    groups = [
        group.model_copy(update={"panel_keys": [k for k in group.panel_keys if k in shown]})
        for group in dashboard.groups
    ]
    return dashboard.model_copy(
        update={"panels": panels, "groups": [g for g in groups if g.panel_keys]}
    )


def focused(dashboard: Dashboard, tags: Sequence[str]) -> Dashboard:
    """`filtered`, for focus mode: an item in a panel that carries none of `tags` itself keeps
    only its parts that carry one, as focus mode steps through each part alone. Act now's items
    are steps whole, and keep theirs."""
    shown = filtered(dashboard, tags)
    if not tags:
        return shown
    wanted = set(tags)
    act_now = shown.find_group(ACT_NOW)
    whole = set(act_now.panel_keys) if act_now else set[str]()

    def trimmed(item: Item) -> Item:
        if any(t in wanted for t in item.tags) or not item.parts:
            return item
        return item.model_copy(update={"parts": [p for p in item.parts if carries(p, wanted)]})

    return shown.model_copy(
        update={
            "panels": [
                p
                if p.key in whole
                else p.model_copy(update={"items": [trimmed(i) for i in p.items]})
                for p in shown.panels
            ]
        }
    )


def options(dashboard: Dashboard) -> list[tuple[str, int]]:
    """Every tag an item on the page carries, by handle, with how many things carry it (an
    entity shown in two panels, or in Act now and its panel, counts once). Give it the
    dashboard before `filtered`, so every tag can be added: its plugins' panels are whole, and
    Act now's items, however it was filtered, are made of their items."""
    carriers: dict[str, set[Ref]] = {}

    def visit(item: Item) -> None:
        for tag in item.tags:  # an item with tags is about something: its badges say what
            carriers.setdefault(tag, set()).add(item.subjects[0])
        for part in item.parts:
            visit(part)

    for panel in dashboard.panels:
        for item in panel.items:
            visit(item)
    return [(tag, len(carriers[tag])) for tag in sorted(carriers)]
