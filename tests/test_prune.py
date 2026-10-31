from datetime import UTC, datetime, timedelta
from pathlib import Path

from move_forward import notes, prune
from move_forward.config import Config
from move_forward.db import CORE, Database
from move_forward.domain import Ping
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.jira.config import JiraConfig
from move_forward.repositories import KvRepository, PingRepository

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=100)
RECENT = NOW - timedelta(days=10)
PR = "https://github.com/acme/widgets/pull/"

WORK_NOTES = f"""\
# Work notes

## APP-1 long done
- Where: {PR}1
- Left to do:
- Done 2026-06-01: shipped
- Updated: 2026-06-01

## APP-2 done but still has a todo
- Where: {PR}2
- Left to do:
  - [quick] write the wrap-up
- Done 2026-06-01: shipped
- Done 2026-10-28: tested
- Updated: 2026-10-28

## APP-3 PR merged long ago, ticket open
- Where: {PR}3
- Left to do:
- Updated: 2026-06-01

## No ticket, no PR, stale
- Left to do:
- Updated: 2026-05-01

## {PR}4 PR without a ticket, merged recently
- Left to do:
- Updated: 2026-10-20
"""


def _config(tmp_path: Path, jira: bool = True) -> Config:
    (tmp_path / notes.WORK_NOTES_FILE).write_text(WORK_NOTES)
    return Config(
        email="me@x",
        github=GitHubConfig(org="acme"),
        notes_dir=tmp_path,
        jira=JiraConfig(site="https://j") if jira else None,
    )


def pr_closed_at(url: str) -> datetime | None:
    return {PR + "1": OLD, PR + "2": OLD, PR + "3": OLD, PR + "4": RECENT}.get(url)


def issues_resolved_at(keys: list[str]) -> dict[str, datetime]:
    return {k: OLD for k in keys if k in ("APP-1", "APP-2")}


def test_prune_notes_clears_finished_entries_and_old_done_lines(tmp_path: Path) -> None:
    config = _config(tmp_path)
    notices = prune.prune_notes(config, NOW, pr_closed_at, issues_resolved_at)
    assert notices == [
        "cleared entry APP-1 long done",
        "dropped 1 old Done line(s) from APP-2 done but still has a todo",
        "cleared entry No ticket, no PR, stale",
    ]
    text = (tmp_path / notes.WORK_NOTES_FILE).read_text()
    titles = [i.title for i in notes.parse_work_notes(text)]
    assert titles == [
        "APP-2 done but still has a todo",
        "APP-3 PR merged long ago, ticket open",
        f"{PR}4 PR without a ticket, merged recently",
    ]
    assert "Done 2026-06-01: shipped" not in text and "Done 2026-10-28: tested" in text
    assert "\n\n\n" not in text
    # A second run has nothing to do and leaves the file alone.
    assert prune.prune_notes(config, NOW, pr_closed_at, issues_resolved_at) == []


def test_entries_with_tickets_are_kept_without_jira(tmp_path: Path) -> None:
    config = _config(tmp_path, jira=False)
    notices = prune.prune_notes(config, NOW, pr_closed_at, None)
    assert notices == [
        "dropped 1 old Done line(s) from APP-1 long done",  # kept: its ticket can't be checked
        "dropped 1 old Done line(s) from APP-2 done but still has a todo",
        "cleared entry No ticket, no PR, stale",
    ]


def test_prune_deletes_old_pings_and_runs_once_a_day(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = Database(":memory:")
    pings = PingRepository(db)
    for when in (OLD, RECENT):
        pings.add(Ping(pr_url=PR + "1", target="user:a", kind="first", message="", sent_at=when))
    notices = prune.prune(config, db, NOW, pr_closed_at, issues_resolved_at)
    assert notices[-1] == "deleted 1 old ping(s)"
    assert [p.sent_at for p in pings.all()] == [RECENT]

    db = Database(":memory:")
    meta = KvRepository(db, CORE)
    pings = PingRepository(db)
    pings.add(Ping(pr_url=PR + "1", target="user:a", kind="first", message="", sent_at=OLD))
    (tmp_path / notes.WORK_NOTES_FILE).unlink()  # nothing to look up: no network needed
    prune.prune_if_due(config, db, NOW)
    assert meta.get(prune.LAST_PRUNE_KEY) == NOW.isoformat() and pings.all() == []
    pings.add(Ping(pr_url=PR + "1", target="user:a", kind="first", message="", sent_at=OLD))
    prune.prune_if_due(config, db, NOW + timedelta(hours=23))
    assert len(pings.all()) == 1  # not due yet
    prune.prune_if_due(config, db, NOW + timedelta(hours=25))
    assert pings.all() == []


def test_plugins_prune_with_the_daily_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    (tmp_path / notes.WORK_NOTES_FILE).unlink()
    db = Database(":memory:")
    runs: list[datetime] = []

    def plugins() -> list[str]:
        runs.append(NOW)
        return []

    prune.prune_if_due(config, db, NOW, plugins)
    prune.prune_if_due(config, db, NOW + timedelta(hours=1), plugins)
    assert len(runs) == 1
