"""A plugin's tests, run as core runs the plugin: `Harness`.

Nothing here imports pytest: wrap a `Harness` in a fixture of your own.

```python
def test_triage_lists_my_open_issues() -> None:
    linear = Harness(LinearPlugin(), {"team": "ENG"})
    linear.store(LinearSnapshot(issues=[issue("ENG-1")]))
    [panel] = linear.panels()
    assert [i.title for i in panel.items] == ["ENG-1 Fix it"]
```
"""

import tempfile
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from . import refresh, registry, render
from .config import Config
from .db import Database
from .domain import KindModel
from .graph import Entity, Graph, KindSpec
from .kinds import FAMILIES, Family, declaring
from .notifications import Event
from .page import Item, Panel, Surface
from .panels import Dashboard
from .plugin import (
    API_VERSION,
    Context,
    EntitiesRequest,
    GraphEventsRequest,
    PanelsRequest,
    Plugin,
    PruneRequest,
    Response,
    StoreRequest,
    page_path,
)
from .repositories import SnapshotRepository
from .strict import Strict

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
"""The time a harness starts at, unless given another."""


class _AnyAttrs(KindModel):
    """What a seeded entity of another plugin's kind may carry: anything."""

    model_config = ConfigDict(extra="allow", frozen=True)


class _NoSettings(Strict):
    """A seed's section: it has nothing to set."""


class _Seed(Plugin[BaseModel, BaseModel]):
    """A stand-in publisher of entities a test places: a family's, or another plugin's kind
    (`<plugin>.<kind>`), as that plugin would publish them."""

    title: ClassVar[str] = "Seeded"
    always_on: ClassVar[bool] = True
    api_version = API_VERSION
    config_model = _NoSettings

    def __init__(self, name: str, entities: Sequence[Entity]):
        self.seeded = list(entities)

    def entities(self, req: EntitiesRequest[BaseModel, BaseModel]) -> list[Entity]:
        return list(self.seeded)


def _seed(name: str, entities: Sequence[Entity]) -> _Seed:
    """A `_Seed` named `name`, declaring the kinds of `entities`."""
    kinds = {
        e.ref.kind: KindSpec(FAMILIES[e.ref.kind].model if e.ref.kind in FAMILIES else _AnyAttrs)
        for e in entities
    }
    cls = type(f"Seed_{name}", (_Seed,), {"name": name, "kinds": kinds})
    return cls(name, entities)


class Harness:
    """A plugin enabled alone (with what `with_entities` seeds) over an in-memory database,
    run as core runs it: the graph core builds, the page it renders, its actions and pages.
    `section` is the plugin's config section, as the TOML would have it; `families`, those
    other plugins declare whose entities it seeds or reads. The plugin must pass core's
    checks (`registry.check_plugins`)."""

    def __init__(
        self,
        plugin: Plugin[Any, Any],
        section: Mapping[str, object] | None = None,
        *,
        now: datetime = NOW,
        email: str = "me@x",
        teams: Sequence[str] = (),
        families: Sequence[Family] = (),
    ):
        self.plugin = plugin
        self.now = now
        self.db = Database(":memory:")
        self._dir = tempfile.TemporaryDirectory(prefix="move-forward-harness-")
        """Where the config's `db_path` points, so nothing reaches the real database's
        folder; gone with the harness."""
        self._section = dict(section or {})
        self._email = email
        self._teams = list(teams)
        self._families = (*plugin.families, *families)
        self._seeds: list[_Seed] = []
        self._previous: tuple[Graph, BaseModel | None] | None = None
        with self._installed():
            registry.check_plugins([plugin])
            self.config = self._config()
            registry.migrate(self.config, self.db)

    # Setting up

    def with_entities(self, entities: Sequence[Entity]) -> "Harness":
        """Place other plugins' entities in the graph: a family's (a `pull_request`, a
        `ticket`) as some plugin would publish them, or another plugin's own kind
        (`git.branch`) as that plugin. Returns the harness, to chain."""
        by_owner: dict[str, list[Entity]] = {}
        with self._installed():
            for entity in entities:
                kind = entity.ref.kind
                if kind not in FAMILIES and "." not in kind:
                    raise ValueError(
                        f"{kind} is neither a family nor a plugin's kind: pass the family that"
                        " another plugin declares as Harness(..., families=...)"
                    )
                owner = "seed" if kind in FAMILIES else kind.split(".", 1)[0]
                by_owner.setdefault(owner, []).append(entity)
            self._seeds += [_seed(owner, found) for owner, found in by_owner.items()]
        with self._installed():  # again, with the new seeds
            self.config = self._config()
        return self

    def store(self, snapshot: BaseModel) -> None:
        """Keep `snapshot` as a refresh would after collecting it: the plugin's `store`, and
        when it was fetched. What the graph held before is kept for `graph_events`."""
        self._previous = (self.graph(), self.snapshot())
        self.plugin.store(StoreRequest(self.ctx, snapshot))
        SnapshotRepository(self.db).save_fetched(self.plugin.name, self.now)

    def tick(self, seconds: float) -> None:
        """Move the harness's clock on."""
        self.now += timedelta(seconds=seconds)

    # Reading

    @property
    def ctx(self) -> Context[Any]:
        """The plugin's context, as core gives it at `now`."""
        with self._installed():
            [enabled] = [
                e for e in registry.enabled(self.config, self.db, self.now) if e.name == self.name
            ]
        return enabled.ctx

    @property
    def name(self) -> str:
        return self.plugin.name

    def snapshot(self) -> BaseModel | None:
        """What the plugin last stored, through its `load`."""
        with self._installed():
            [enabled] = [
                e for e in registry.enabled(self.config, self.db, self.now) if e.name == self.name
            ]
            return enabled.snapshot()

    def graph(self) -> Graph:
        """The graph core builds from the plugin's and the seeded entities."""
        with self._installed():
            graph, _ = refresh.stored_graph(
                self.db, registry.enabled(self.config, self.db, self.now)
            )
        return graph

    def entities(self) -> list[Entity]:
        """What the plugin publishes, as the graph holds it: core's checks passed."""
        graph = self.graph()
        with self._installed():
            return [
                entity
                for kind in self.plugin.kinds
                for entity in graph.of_kind(f"{self.name}.{kind}" if kind in FAMILIES else kind)
            ]

    def panels(self) -> list[Panel]:
        """The plugin's panels, as its `panels` makes them (before core adds buttons)."""
        return self.plugin.panels(PanelsRequest(self.ctx, self.snapshot(), self.graph()))

    def dashboard(self, surface: Surface = "dashboard") -> Dashboard:
        """The page core builds: Act now, the panels with every plugin's buttons, hidden
        items left out."""
        with self._installed():
            return refresh.build_dashboard(self.config, self.db, self.now, surface=surface)

    def act_now(self) -> list[Item]:
        """Act now's items, ranked."""
        return self.dashboard().act_now()

    def render_item(self, item: Item) -> str:
        """An item's HTML, as the page shows it."""
        return render.item_html(item)

    # Doing

    def run_action(self, name: str, fields: dict[str, str]) -> str:
        """Press one of the plugin's buttons: its action `name` with the form's `fields`.
        Returns the notice; raises `ActionError` as the page would show it."""
        with self._installed():
            return refresh.run_action(self.config, self.db, f"{self.name}/{name}", fields)

    def page(self, key: str, query: dict[str, list[str]] | None = None) -> str | Response | None:
        """One of the plugin's pages, by its key (`""` its own), with a query."""
        with self._installed():
            return refresh.plugin_page(self.config, self.db, page_path(self.name, key), query or {})

    def graph_events(self) -> list[Event]:
        """What the plugin's `graph_events` says changed between the graph before the last
        `store` and now."""
        if self._previous is None:
            raise RuntimeError("store a snapshot first: the events are of what it changed")
        previous_graph, previous_snapshot = self._previous
        return self.plugin.graph_events(
            GraphEventsRequest(
                self.ctx, self.graph(), self.snapshot(), previous_graph, previous_snapshot
            )
        )

    def prune(self) -> list[str]:
        """Run the plugin's daily prune now; what it says it did."""
        return self.plugin.prune(PruneRequest(self.ctx, self.graph()))

    # Helpers

    def _config(self) -> Config:
        return Config.model_validate(
            {
                "email": self._email,
                "teams": self._teams,
                "db_path": Path(self._dir.name) / "dashboard.db",
                self.name: self._section,
            }
        )

    @contextmanager
    def _installed(self) -> Generator[None]:
        """Core sees only the plugin and the seeds, and the plugin's and the given families,
        while a call runs."""
        saved = registry.PLUGINS
        registry.PLUGINS = [self.plugin, *self._seeds]
        try:
            with declaring(self.name, self._families, keep=False):
                yield
        finally:
            registry.PLUGINS = saved
