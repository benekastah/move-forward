"""The plugin protocol: what core asks of each external tool's plugin.

A plugin owns one config section, one snapshot (what it fetches on each refresh), its own
tables, and the panels, actions, pages and commands for its tool. Plugins never see each
other's config, snapshots or tables; they learn about each other's data only through the
entity graph (`graph`). Every method has a do-nothing default, so a plugin implements only
what it uses.
"""

import argparse
import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from . import shell
from .db import Database
from .graph import Entity, Graph
from .page import Action, Item, Panel
from .repositories import KvRepository, SnapshotRepository

log = logging.getLogger(__name__)

Check = Callable[[str], str]
"""Validates an answer: returns it (possibly normalised) or raises ValueError with the reason."""


class Asker(Protocol):
    """Asks one `mfd init` question, again until `check` accepts the answer."""

    def __call__(self, question: str, default: str, check: Check | None = None) -> str: ...


@dataclass(frozen=True)
class Context[C: BaseModel]:
    """What a plugin gets to work with: its own config section and storage, and the
    settings every plugin shares."""

    config: C
    """The plugin's `[name]` section."""
    email: str
    """My work email."""
    teams: list[str]
    """The names of the teams I'm on or review for; the plugin maps them to its tool's own
    names in its section's `teams` table."""
    prune_days: int
    """Work finished this long ago is cleared by `prune`."""
    now: datetime
    db: Database
    """For the plugin's own repository over its own tables, which its migrations made."""
    kv: KvRepository
    """Key/value state in the plugin's namespace."""


PageHandler = Callable[[Context[Any], Graph, dict[str, list[str]]], str | None]
"""Renders a page from the query parameters; None means not found."""
CliHandler = Callable[[Context[Any], Graph, argparse.Namespace], int]
"""Runs `mfd <plugin> ...` with the parsed arguments; returns the exit status."""
ActionHandler = Callable[[Context[Any], Graph, dict[str, str]], str]
"""Runs a button's action with its form fields; returns the notice for the page and raises
`ActionError` with the reason when it can't."""


class ActionError(Exception):
    """Something the user needs to know; shown on the page, not a crash."""


class Plugin[C: BaseModel, S: BaseModel]:
    """Base of every plugin. Subclasses set the class attributes and override what they use.

    C is the config section's model; S the snapshot's (`BaseModel` when there is none).
    """

    name: ClassVar[str]
    """e.g. "github": the config section, snapshot source, table prefix and URL segment."""
    title: ClassVar[str]
    """e.g. "GitHub": the heading of the plugin's group on the page."""
    config_model: ClassVar[type[BaseModel]]
    """The model of the `[name]` section: C."""
    requires: ClassVar[list[str]] = []
    """Plugins whose entities this one is built on, e.g. `["git"]`; the config must enable
    them too."""

    migrations: ClassVar[list[str]] = []
    """SQL scripts creating and changing the plugin's tables, applied in order. Append to it;
    never edit one that has shipped. Tables are prefixed with `name`."""
    snapshot_model: ClassVar[type[BaseModel] | None] = None
    """S, or None when the plugin fetches nothing and `collect` is never called."""
    collects_after_graph: ClassVar[bool] = False
    """Whether the snapshot comes from `collect_after` instead of `collect`."""

    # Refresh

    def collect(self, ctx: Context[C]) -> S:
        """Fetch the plugin's snapshot. Runs in parallel with other plugins' and may raise;
        the error is shown and the last good snapshot kept."""
        raise NotImplementedError(f"{self.name} has a snapshot model but no collect")

    def collect_after(self, ctx: Context[C], graph: Graph) -> S:
        """Fetch the snapshot when what to fetch comes from other plugins' entities, e.g. the
        repos git found. Runs once their snapshots are stored, in parallel with other
        `collect_after`s, and fails like `collect`."""
        raise NotImplementedError(f"{self.name} collects after the graph but has no collect_after")

    def after_collect(self, ctx: Context[C], graph: Graph) -> None:
        """Work that needs other plugins' entities, once every snapshot is stored, e.g.
        caching lookups in the plugin's tables."""

    # Reading

    def entities(self, ctx: Context[C], snapshot: S | None) -> list[Entity]:
        """What the plugin publishes to the graph, from its snapshot and tables."""
        return []

    def url_for(self, ctx: Context[C], kind: str, id: str) -> str | None:
        """A URL for an entity of a kind the plugin knows, even one not in the graph, e.g. a
        ticket's from its key."""
        return None

    # Page

    def panels(self, ctx: Context[C], snapshot: S | None, graph: Graph) -> list[Panel]:
        return []

    def item_actions(self, ctx: Context[C], item: Item, graph: Graph) -> list[Action]:
        """Buttons the plugin adds to another plugin's item, e.g. "add todo" on a PR."""
        return []

    def pages(self) -> dict[str, PageHandler]:
        """Extra pages by path, e.g. "/ping"."""
        return {}

    # Writing

    def actions(self) -> dict[str, ActionHandler]:
        """Button handlers by name, posted to `/actions/<plugin>/<name>`."""
        return {}

    cli_help: ClassVar[str | None] = None
    """The help line of `mfd <name>`; None means the plugin has no command."""

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        """Add the arguments of `mfd <name>` to `parser` and return what runs it. Only called
        when `cli_help` is set."""
        raise NotImplementedError(f"{self.name} has cli_help but no cli")

    # Set-up and upkeep

    def init_questions(self, ask: Asker) -> dict[str, object]:
        """Ask for the `[name]` section's settings in `mfd init`; returns its values."""
        return {}

    def prune(self, ctx: Context[C], graph: Graph) -> list[str]:
        """Daily: drop the plugin's data about work finished over `prune_days` ago. Returns
        what was done, one line each."""
        return []


def collect_into[T: BaseModel](
    db: Database, source: str, now: datetime, collector: Callable[[], T]
) -> T | None:
    """Run a collector and store its result as the `source` snapshot, or its error next to
    the last good result. Returns the result, or None if it failed."""
    snapshots = SnapshotRepository(db)
    try:
        result = collector()
    except Exception as e:  # noqa: BLE001 - one failing source must not stop the others
        if shell.stopping():
            return None  # ctrl-c interrupted the commands; nothing to record
        log.warning("%s failed: %s", source, e)
        log.debug("%s", traceback.format_exc())
        snapshots.save_error(source, now, f"{type(e).__name__}: {e}")
        return None
    snapshots.save(source, now, result)
    return result
