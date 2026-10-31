"""The daily clean-up: work finished longer ago than `prune_days` leaves the notes and the DB.

From work-notes.md: `- Done <date>:` lines that old, and whole entries whose work is over
(every PR merged or closed, every ticket resolved, no open todos) that long ago. From the
database: pings that old. It runs once a day from a refresh; see `prune_if_due`.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from .actions import entry_bounds, left_to_do
from .config import Config
from .db import CORE, Database
from .notes import WORK_NOTES_FILE
from .panels import JIRA_KEY_RE, PR_URL_RE
from .plugins.github import client as github
from .plugins.jira import client as jira
from .repositories import KvRepository, PingRepository

log = logging.getLogger(__name__)

LAST_PRUNE_KEY = "last_prune"
PRUNE_EVERY = timedelta(days=1)
DONE_LINE_RE = re.compile(r"^- Done (\d{4}-\d{2}-\d{2})\b")
UPDATED_LINE_RE = re.compile(r"^- Updated: (\d{4}-\d{2}-\d{2})\b")

PrClosedAt = Callable[[str], datetime | None]
"""When a PR was merged or closed, by URL; None while it's open (or unknown)."""
IssuesResolvedAt = Callable[[list[str]], dict[str, datetime]]
"""When each of the given tickets was resolved; unresolved ones are left out."""


def _date(line: str, pattern: re.Pattern[str]) -> date | None:
    match = pattern.match(line)
    return date.fromisoformat(match.group(1)) if match else None


def _entry_finished(
    lines: list[str],
    start: int,
    end: int,
    cutoff: datetime,
    pr_closed_at: PrClosedAt,
    issues_resolved_at: IssuesResolvedAt | None,
) -> bool:
    """Whether an entry's work was all over by `cutoff`, so the entry can go."""
    block = left_to_do(lines, start, end)
    if block and block[1] > block[0] + 1:
        return False  # open todos
    text = "\n".join(lines[start:end])
    urls = sorted(set(PR_URL_RE.findall(text)))
    keys = sorted(set(JIRA_KEY_RE.findall(lines[start])))
    for url in urls:
        closed = pr_closed_at(url)
        if closed is None or closed > cutoff:
            return False
    if keys:
        if issues_resolved_at is None:
            return False  # can't check the tickets without Jira
        resolved = issues_resolved_at(keys)
        if any(key not in resolved or resolved[key] > cutoff for key in keys):
            return False
    if not urls and not keys:
        updated = next(
            (d for line in lines[start:end] if (d := _date(line, UPDATED_LINE_RE))), None
        )
        if updated is None or datetime.combine(updated, datetime.min.time(), UTC) > cutoff:
            return False
    return True


def prune_notes(
    config: Config,
    now: datetime,
    pr_closed_at: PrClosedAt,
    issues_resolved_at: IssuesResolvedAt | None,
) -> list[str]:
    """Drop finished entries and old Done lines from work-notes.md; returns what was done."""
    path = config.notes_dir / WORK_NOTES_FILE
    if not path.exists():
        return []
    cutoff = now - timedelta(days=config.prune_days)
    lines = path.read_text().split("\n")
    notices: list[str] = []
    titles = [line[3:].strip() for line in lines if line.startswith("## ")]
    for title in titles:
        start, end = entry_bounds(lines, title)
        if _entry_finished(lines, start, end, cutoff, pr_closed_at, issues_resolved_at):
            del lines[start:end]
            while (
                start > 0
                and start < len(lines)
                and not lines[start].strip()
                and not lines[start - 1].strip()
            ):
                del lines[start]
            notices.append(f"cleared entry {title}")
            continue
        kept: list[str] = []
        dropped = 0
        for line in lines[start:end]:
            done = _date(line, DONE_LINE_RE)
            if done and datetime.combine(done, datetime.min.time(), UTC) <= cutoff:
                dropped += 1
            else:
                kept.append(line)
        if dropped:
            lines[start:end] = kept
            notices.append(f"dropped {dropped} old Done line(s) from {title}")
    if notices:
        path.write_text("\n".join(lines).rstrip("\n") + "\n")
    return notices


def _jira_lookup(config: Config) -> IssuesResolvedAt | None:
    if config.jira is None:
        return None
    jira_config = config.jira

    def lookup(keys: list[str]) -> dict[str, datetime]:
        return jira.resolved_at(
            jira_config, config.email, keys, jira.token(jira_config, config.email)
        )

    return lookup


def prune(
    config: Config,
    db: Database,
    now: datetime,
    pr_closed_at: PrClosedAt = github.pr_closed_at,
    issues_resolved_at: IssuesResolvedAt | None = None,
) -> list[str]:
    """Prune the notes and the database once; returns what was done."""
    notices = prune_notes(config, now, pr_closed_at, issues_resolved_at or _jira_lookup(config))
    deleted = PingRepository(db).delete_before(now - timedelta(days=config.prune_days))
    if deleted:
        notices.append(f"deleted {deleted} old ping(s)")
    return notices


def _no_plugins() -> list[str]:
    return []


def prune_if_due(
    config: Config, db: Database, now: datetime, plugins: Callable[[], list[str]] = _no_plugins
) -> None:
    """Run `prune`, then `plugins` (the plugins' prunes), if they haven't run for a day. A
    failure is logged and retried the next day."""
    meta = KvRepository(db, CORE)
    last = meta.get(LAST_PRUNE_KEY)
    if last and datetime.fromisoformat(last) + PRUNE_EVERY > now:
        return
    meta.set(LAST_PRUNE_KEY, now.isoformat())
    try:
        for notice in prune(config, db, now):
            log.info("prune: %s", notice)
    except Exception:
        log.exception("prune failed")
    for notice in plugins():
        log.info("prune: %s", notice)
