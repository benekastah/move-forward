"""The daily clean-up: once a day, after a refresh, each plugin's `prune` drops its data
about work finished more than `prune_days` ago (old notes, old pings). See `prune_if_due`."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from .db import CORE, Database
from .repositories import KvRepository

log = logging.getLogger(__name__)

LAST_PRUNE_KEY = "last_prune"
PRUNE_EVERY = timedelta(days=1)


def prune_if_due(db: Database, now: datetime, plugins: Callable[[], list[str]]) -> None:
    """Run `plugins` (the plugins' prunes) if they haven't run for a day, logging what they
    did. A failure is logged by the plugin that failed; the next run is a day later."""
    meta = KvRepository(db, CORE)
    last = meta.get(LAST_PRUNE_KEY)
    if last and datetime.fromisoformat(last) + PRUNE_EVERY > now:
        return
    meta.set(LAST_PRUNE_KEY, now.isoformat())
    for notice in plugins():
        log.info("prune: %s", notice)
