"""Notification events of the git plugin: local branches that can be deleted.

Whether a branch can be deleted depends on its PR having merged, which github knows, so it's
read from the Cleanup panel as built from the graph (`Plugin.graph_events`).
"""

from ...api import Event, Panel

KINDS = {"branch_deletable": "a local branch's PR is merged (or its remote branch is gone)"}
"""The kinds, with what each says, for the settings page."""


def _targets(cleanup: Panel) -> dict[str, Event]:
    """The branches the Cleanup panel lists, by `<repo>:<branch>`, each with its event."""
    found: dict[str, Event] = {}
    for item in cleanup.items:
        if not item.actions:
            continue
        fields = item.actions[0].fields
        key = f"{fields['repo']}:{fields['branch']}"
        found[key] = Event(
            kind="branch_deletable",
            thing=f"branch:{key}",
            title=item.title,
            text="can be deleted",
            anchor=item.anchor or "",
        )
    return found


def newly_deletable(before: Panel, now: Panel) -> list[Event]:
    """Events for the branches in the Cleanup panel `now` that weren't `before`; a branch that
    leaves the panel and comes back is said again."""
    known = _targets(before)
    return [event for key, event in _targets(now).items() if key not in known]
