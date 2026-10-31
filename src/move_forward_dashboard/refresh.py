"""Runs the collectors, stores their snapshots, and builds the dashboard from what's stored."""

import logging
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from pydantic import BaseModel

from . import derive, github, jira, local, notes, panels, shell
from .config import Config
from .db import Database
from .domain import DerivedSnapshot, GitHubSnapshot, JiraSnapshot, LocalSnapshot, TeamChannel
from .prune import prune_if_due
from .repositories import (
    IcedStackRepository,
    NotesImportRepository,
    PingRepository,
    ReviewerRepository,
    SnapshotRepository,
    WorkItemRepository,
)

log = logging.getLogger(__name__)

SOURCE_GITHUB = "github"
SOURCE_LOCAL = "local"
SOURCE_JIRA = "jira"
SOURCE_DERIVED = "derived"


def _collect[T: BaseModel](
    snapshots: SnapshotRepository, source: str, now: datetime, collector: Callable[[], T]
) -> T | None:
    """Run one collector, store its result or error, and return the result."""
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


def _lookup_emails(config: Config, db: Database, gh: GitHubSnapshot) -> None:
    """Cache the commit email of every requested reviewer we don't know yet."""
    if not config.reviewer_email_domains:
        return
    reviewers = ReviewerRepository(db)
    known = reviewers.users()
    for pr in gh.mine:
        for login in pr.requested_users:
            if login in known:
                continue
            try:
                email = github.commit_email(pr.repo, login, config.reviewer_email_domains)
            except Exception as e:  # noqa: BLE001
                log.warning("email lookup for %s failed: %s", login, e)
                continue
            reviewers.save_looked_up_email(login, email)
            known[login] = reviewers.users()[login]


def refresh(config: Config, db: Database, now: datetime | None = None) -> None:
    """Import the notes and refresh every source. Each source fails on its own."""
    now = now or datetime.now(UTC)
    snapshots = SnapshotRepository(db)
    NotesImportRepository(db).replace(notes.load(config.notes_dir))

    with ThreadPoolExecutor(max_workers=3) as pool:
        gh_future = pool.submit(
            _collect,
            snapshots,
            SOURCE_GITHUB,
            now,
            lambda: github.collect(
                config.github.org, config.team_slugs, config.github.history_days, now
            ),
        )
        local_future = pool.submit(
            _collect,
            snapshots,
            SOURCE_LOCAL,
            now,
            lambda: local.collect(
                config.repos, now, config.commit_history_days, config.ignored_worktree_globs
            ),
        )
        if config.jira is not None:
            jira_config = config.jira
            pool.submit(
                _collect,
                snapshots,
                SOURCE_JIRA,
                now,
                lambda: jira.collect(jira_config, config.email),
            )
    gh = gh_future.result()
    local_snapshot = local_future.result()
    if not shell.stopping():
        prune_if_due(config, db, now)
    if gh is not None:
        _lookup_emails(config, db, gh)
    if gh is not None and local_snapshot is not None:
        github_snapshot, local_result = gh, local_snapshot
        _collect(
            snapshots,
            SOURCE_DERIVED,
            now,
            lambda: derive.collect(
                github_snapshot,
                local_result,
                ReviewerRepository(db).users(),
                config.github.org,
                config.team_slugs,
                config.commit_history_days,
                config.github.required_owner_marker,
            ),
        )


def _team_channels(config: Config, known: dict[str, TeamChannel]) -> dict[str, TeamChannel]:
    """The reviewer map's team channels, with the config's `slack_channel` winning by name."""
    teams = dict(known)
    for slug, team in config.teams.items():
        if team.slack_channel:
            current = teams.get(slug)
            teams[slug] = TeamChannel(
                slug=slug,
                channel_name=team.slack_channel,
                channel_id=current.channel_id if current else None,
            )
    return teams


def build_dashboard(config: Config, db: Database, now: datetime | None = None) -> panels.Dashboard:
    """The dashboard from whatever is stored; cheap, no network."""
    now = now or datetime.now(UTC)
    snapshots = SnapshotRepository(db)
    gh = snapshots.get(SOURCE_GITHUB, GitHubSnapshot)
    inputs = panels.Inputs(
        now=now,
        login=gh.login if gh else "",
        my_teams=config.team_slugs,
        github=gh,
        local=snapshots.get(SOURCE_LOCAL, LocalSnapshot),
        jira=snapshots.get(SOURCE_JIRA, JiraSnapshot),
        derived=snapshots.get(SOURCE_DERIVED, DerivedSnapshot),
        work_items=WorkItemRepository(db).all(),
        iced_stacks=IcedStackRepository(db).all(),
        users=ReviewerRepository(db).users(),
        teams=_team_channels(config, ReviewerRepository(db).teams()),
        pings=PingRepository(db).all(),
        sources={
            source: panels.SourceStatus(fetched_at=row.fetched_at, error=row.error)
            for source, row in snapshots.all().items()
        },
        slack_workspace_url=config.slack.workspace_url,
        jira_site=config.jira.site if config.jira else "",
        stale_review_hours=config.stale_review_hours,
        review_checks=config.github.review_checks,
        my_branch_prefixes=config.my_branch_prefixes,
        ping_first=config.slack.ping_first,
        ping_follow_up=config.slack.ping_follow_up,
        placeholder_labels=config.github.placeholder_labels,
        ticket_link_prefix=config.github.ticket_link_prefix,
    )
    if config.jira:
        inputs = inputs.model_copy(
            update={
                "jira_statuses": panels.JiraStatuses(
                    in_progress=config.jira.status_in_progress,
                    in_review=config.jira.status_in_review,
                    closed=config.jira.status_closed,
                ),
                "escalation_label_prefix": config.jira.escalation_label_prefix,
                "escalation_levels": config.jira.escalation_levels,
                "incident_types": config.jira.incident_types,
                "bug_types": config.jira.bug_types,
            }
        )
    return panels.build(inputs)
