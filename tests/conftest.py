"""Shared factories: small, fully populated domain objects that tests tweak."""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel

from move_forward import auth, localtime
from move_forward.config import DEFAULT_DB_PATH, Config
from move_forward.db import Database
from move_forward.domain import Model
from move_forward.notifications import Event
from move_forward.page import Panel
from move_forward.panels import Dashboard
from move_forward.plugin import Previous, Request
from move_forward.plugins.git.models import Branch, LocalRepo, LocalSnapshot
from move_forward.plugins.git.plugin import GitPlugin
from move_forward.plugins.git_stack.models import GitStackSnapshot, StackBranch, StackRepo
from move_forward.plugins.github.models import (
    Check,
    DerivedSnapshot,
    GitHubSnapshot,
    PullRequest,
    Review,
    Thread,
    TimelineEvent,
)
from move_forward.plugins.github.people import PeopleRepository
from move_forward.plugins.github.snapshots import DerivedRepository
from move_forward.plugins.jira.models import JiraIssue
from move_forward.plugins.notes.models import Entry, Ice, IcedItem, Size, Todo
from move_forward.plugins.notes.repository import MIGRATIONS as NOTES_MIGRATIONS
from move_forward.plugins.notes.text import attach_conditions, links_in
from move_forward.plugins.slack.models import Ping, TeamChannel
from move_forward.plugins.slack.pings import PrPing
from move_forward.plugins.slack.plugin import pinger
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.refresh import (
    build_dashboard,
    events_of,
    newly_shown_of,
    store_snapshot,
    stored_graph,
)
from move_forward.registry import BUILTINS, enabled, migrate
from move_forward.repositories import SnapshotRepository

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
ME = "me"
REPO = "acme/widgets"


@pytest.fixture(autouse=True)
def in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test runs in UTC, so what the app takes in local time is the same anywhere."""
    monkeypatch.setattr(localtime, "zone", UTC)


REAL_DATA_DIR = DEFAULT_DB_PATH.expanduser().parent
"""Where the dashboard I use keeps its database and sign-in token, which no test
may touch. Read when a guard runs, so a test of the guard can point it elsewhere."""


def _refuse_real(path: Path | str) -> None:
    if path != ":memory:" and Path(path).expanduser().resolve().is_relative_to(
        REAL_DATA_DIR.resolve()
    ):
        raise AssertionError(f"a test opened the real database's dir, {path}: use tmp_path")


def _guarded[**A, R](call: Callable[A, R], at: int) -> Callable[A, R]:
    """`call`, refusing a path in the real data dir as its `at`th argument."""

    def guarded(*args: A.args, **kwargs: A.kwargs) -> R:
        _refuse_real(cast(Path | str, args[at]))
        return call(*args, **kwargs)

    return guarded


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test's database is its own, in memory or in `tmp_path`: one that would open the
    real one (a config left at its default, say) fails instead of touching my data. So does
    one reading or writing the token beside it, which comes first."""
    monkeypatch.setattr(Database, "__init__", _guarded(Database.__init__, 1))
    monkeypatch.setattr(auth, "load_or_create", _guarded(auth.load_or_create, 0))
    monkeypatch.setattr(auth, "rotate", _guarded(auth.rotate, 0))


@pytest.fixture(autouse=True)
def git_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry's git plugin polls rather than watching the repos, so no test leaves
    watcher threads behind; tests of watching make a plugin of their own."""
    for plugin in BUILTINS:
        if isinstance(plugin, GitPlugin):
            monkeypatch.setattr(plugin, "_watching", False)
            monkeypatch.setattr(plugin, "_watcher", None)


def pr(number: int = 1, **overrides: object) -> PullRequest:
    """An open, non-draft, green, unreviewed PR of mine on main, two days old."""
    created = NOW - timedelta(days=2)
    fields: dict[str, object] = {
        "url": f"https://github.com/{REPO}/pull/{number}",
        "number": number,
        "title": f"PR {number}",
        "repo": REPO,
        "default_branch": "main",
        "is_draft": False,
        "state": "OPEN",
        "created_at": created,
        "updated_at": created,
        "head": f"me/APP-{number}-branch",
        "head_oid": "a" * 40,
        "base": "main",
        "author": ME,
        "size": 10,
        "changed_files": 1,
        "mergeable": "MERGEABLE",
        "merge_state": "CLEAN",
        "review_decision": "REVIEW_REQUIRED",
        "checks": [Check(name="tests", state="SUCCESS", url="https://ci/1")],
        "rollup_state": "SUCCESS",
        "last_commit_at": created,
        "body": f"Link https://jira/browse/APP-{number}",
    }
    fields.update(overrides)
    return PullRequest.model_validate(fields)


def approved_review(author: str = "alice", when: datetime = NOW) -> Review:
    return Review(author=author, state="APPROVED", submitted_at=when)


def thread(author: str = "alice", resolved: bool = False, outdated: bool = False) -> Thread:
    return Thread(
        resolved=resolved,
        outdated=outdated,
        path="src/x.py",
        author=author,
        body="Please rename this",
        url="https://github.com/acme/widgets/pull/1#discussion_r1",
        created_at=NOW - timedelta(hours=1),
    )


def event(type: str, at: datetime, **fields: object) -> TimelineEvent:
    return TimelineEvent.model_validate({"type": type, "at": at, **fields})


def snapshot(**overrides: object) -> GitHubSnapshot:
    fields: dict[str, object] = {
        "login": ME,
        "mine": [],
        "direct_requests": [],
        "reviewed_by_me": [],
        "team_requests": [],
        "merged_recent": [],
        "team_members": [ME, "alice"],
        "team_memberships": {"acme/core": ["alice", ME]},
    }
    fields.update(overrides)
    return GitHubSnapshot.model_validate(fields)


SLACK_OVERRIDES = (
    "slack",
    "slack_workspace_url",
    "ping_first",
    "ping_follow_up",
    "users",
    "teams",
    "pings",
)
"""Overrides that turn the slack plugin on; without one, there's no `[slack]`."""


def stored(**overrides: object) -> tuple[Config, Database]:
    """A config and a database holding stored snapshots and notes. Overrides name a snapshot
    (`github`, `local`, `stacks`, `jira`, `derived`), notes (`work_items`, `iced_stacks`,
    `users`, `teams`, `pings`) or a setting of a plugin's section. github and git are always
    on; git_stack, jira and slack when an override is about them."""
    o = dict(overrides)
    enable_slack = any(key in o for key in SLACK_OVERRIDES)
    o.pop("slack", None)
    db = Database(":memory:")
    snapshots: dict[str, BaseModel] = {}
    for source, key in (
        ("github", "github"),
        ("git", "local"),
        ("git_stack", "stacks"),
        ("jira", "jira"),
    ):
        if (value := o.pop(key, None)) is not None:
            snapshots[source] = cast(BaseModel, value)
    derived = cast(DerivedSnapshot | None, o.pop("derived", None))
    db.migrate("notes", NOTES_MIGRATIONS)
    keep_notes(
        db, cast(list[Entry], o.pop("work_items", [])), cast(list[Ice], o.pop("iced_stacks", []))
    )
    reviewers = cast(dict[str, Reviewer], o.pop("users", {}))
    channels = cast(dict[str, TeamChannel], o.pop("teams", {}))
    pings = cast(list[Ping], o.pop("pings", []))
    github: dict[str, object] = {"org": "acme"}
    for key in (
        "policy_checks",
        "default_team_reviewer_assignment",
        "team_reviewer_assignment",
        "primary_reviewer_repos",
        "ignored_reviewers",
        "placeholder_labels",
        "ticket_link_prefixes",
        "stale_review_hours",
        "merge_queue_eviction_days",
        "stale_draft_days",
        "quick_review_max_lines",
        "quick_review_max_files",
        "hour_review_max_lines",
        "hour_review_max_files",
        "split_pr_suggest_lines",
        "split_pr_lines",
        "split_pr_labels",
        "merge",
    ):
        if key in o:
            github[key] = o.pop(key)
    git: dict[str, object] = {"repos": [], "my_branch_prefixes": o.pop("my_branch_prefixes", [])}
    slack: dict[str, object] = {"workspace_url": o.pop("slack_workspace_url", "")}
    for key in ("ping_first", "ping_follow_up"):
        if key in o:
            slack[key] = o.pop(key)
    jira: dict[str, object] = {}
    if "jira_site" in o:
        jira["site"] = o.pop("jira_site")
    for key in ("escalation_label_prefix", "escalation_levels", "incident_types", "bug_types"):
        if key in o:
            jira[key] = o.pop(key)
    if (statuses := o.pop("jira_statuses", None)) is not None:
        jira.update({f"status_{k}": v for k, v in cast(dict[str, str], statuses).items()})
    enable_git_stack = "git_stack" in snapshots
    enable_jira = bool(jira) or "jira" in snapshots
    if enable_jira:
        jira.setdefault("site", "https://jira")
    teams = o.pop("my_teams", ["core"])
    if o:
        raise TypeError(f"unknown overrides: {', '.join(o)}")
    config = Config.model_validate(
        {
            "email": "me@acme.com",
            "teams": teams,
            "github": github,
            "git": git,
            **({"git_stack": {}} if enable_git_stack else {}),
            **({"slack": slack} if enable_slack else {}),
            **({"jira": jira} if enable_jira else {}),
        }
    )
    migrate(config, db)
    if enable_jira:
        know_jira_projects(db, "APP")
    for e in enabled(config, db, NOW):  # kept as a refresh keeps them
        if e.name in snapshots:
            store_snapshot(e, snapshots[e.name])
            SnapshotRepository(db).save_fetched(e.name, NOW)
    plugins = enabled(config, db, NOW)
    notes = next(e for e in plugins if e.name == "notes")
    notes.plugin.after_collect(Request(notes.ctx, stored_graph(db, plugins)[0], None))  # ice covers
    if derived is not None:
        DerivedRepository(db).replace(derived)
        SnapshotRepository(db).save_fetched("github.derived", NOW)
    people = PeopleRepository(db)
    for u in reviewers.values():
        if u.email:
            people.save_looked_up(u.login, u.email, NOW)
    if enable_slack:
        slack_repo = SlackRepository(db)
        for u in reviewers.values():
            if u.slack_id:
                slack_repo.set_contact(u.login, u.slack_id)
        for channel in channels.values():
            slack_repo.set_team_channel(channel.slug, channel.channel_name, channel.channel_id)
        for ping in pings:
            slack_repo.add_ping(ping)
    return config, db


def dashboard(**overrides: object) -> Dashboard:
    """The dashboard core builds from what `stored` stores, as `refresh.build_dashboard` does."""
    return build_dashboard(*stored(**overrides), NOW)


def events_between(plugin: str, before: dict[str, object], after: dict[str, object]) -> list[Event]:
    """What core finds of a plugin's events (subscribed to or not) between a refresh that
    stored `before` and one that stored `after`, each `stored`'s overrides: those its
    `events` makes, and its items newly shown with a notification (`Item.notify`) since the
    panels `before` showed."""
    (_, db_before), (config, db) = stored(**before), stored(**after)
    previous, previous_snapshots = stored_graph(db_before, enabled(config, db_before, NOW))
    plugins = enabled(config, db, NOW)
    graph, snapshots = stored_graph(db, plugins)
    e = next(e for e in plugins if e.name == plugin)
    newly_shown_of(e, previous_snapshots[plugin], previous)  # what showed before, to compare
    was = Previous(snapshot=previous_snapshots[plugin], graph=previous)
    return events_of(e, was, snapshots[plugin], graph)


def pings(**overrides: object) -> dict[str, PrPing]:
    """What the slack plugin would ping about, by PR URL, over what `stored` stores."""
    config, db = stored(slack=True, **overrides)
    plugins = enabled(config, db, NOW)
    graph, _ = stored_graph(db, plugins)
    slack = next(e for e in plugins if e.name == "slack")
    return pinger(slack.ctx, graph).pings()


def panel(dashboard: Dashboard, key: str) -> Panel:
    return next(p for p in dashboard.panels if p.key == key)


def titles(dashboard: Dashboard, key: str) -> list[str]:
    return [item.title for item in panel(dashboard, key).items]


def issue(key: str = "APP-1", **overrides: object) -> JiraIssue:
    fields: dict[str, object] = {
        "key": key,
        "summary": "Do the thing",
        "status": "In Progress",
        "status_category": "indeterminate",
        "type": "Task",
        "created": NOW - timedelta(days=10),
        "updated": NOW - timedelta(days=1),
        "url": f"https://jira/browse/{key}",
    }
    fields.update(overrides)
    return JiraIssue.model_validate(fields)


class Reviewer(Model):
    """Someone who reviews: their GitHub login, commit email (github) and Slack id (slack)."""

    login: str
    email: str | None = None
    slack_id: str | None = None


def todo(text: str, size: Size | None = None) -> Todo:
    """An open todo."""
    return Todo(id=0, text=text, size=size)


def entry(title: str, todos: list[Todo], **fields: list[str]) -> Entry:
    """An entry: `fields` are its `Status`, `Where`, `Blocked on`, `Updated` (a day, as
    `YYYY-MM-DD`) and `Merge conditions`,
    e.g. `**{"Blocked on": ["reviews"]}`. It's about the PRs its title, fields and todos
    name, and each condition holds the PR it names, else each of those."""
    known = {key: values[0] for key, values in fields.items() if values}
    written = fields.get("Merge conditions", [])
    named = [title, *(v for values in fields.values() for v in values), *(t.text for t in todos)]
    links = links_in("\n".join(named))
    conditions, lines = attach_conditions(written, [link.ref for link in links])
    return Entry(
        id=0,
        title=title,
        status=known.get("Status"),
        where=known.get("Where"),
        blocked_on=known.get("Blocked on"),
        updated_on=date.fromisoformat(known["Updated"]) if "Updated" in known else None,
        todos=[t.model_copy(update={"links": links_in(t.text)}) for t in todos],
        conditions=conditions,
        lines=lines,
        links=links,
    )


def keep_notes(db: Database, entries: list[Entry], ices: list[Ice]) -> None:
    """Put `entries` and `ices` in the notes' tables, with ids of their own."""
    with db.transaction() as conn:
        for e in entries:
            entry_id = conn.execute(
                "INSERT INTO notes_entries (title, status, location, blocked_on, updated_on)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    e.title,
                    e.status,
                    e.where,
                    e.blocked_on,
                    e.updated_on.isoformat() if e.updated_on else None,
                ),
            ).lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO notes_links VALUES (?, ?, ?)",
                [(entry_id, link.kind, link.ref) for link in e.links],
            )
            conn.executemany(
                "INSERT INTO notes_entry_lines VALUES (?, ?, ?)",
                [(entry_id, i, line) for i, line in enumerate(e.lines)],
            )
            conn.executemany(
                "INSERT INTO notes_conditions (entry_id, position, text, pr) VALUES (?, ?, ?, ?)",
                [(entry_id, i, c.text, c.pr) for i, c in enumerate(e.conditions)],
            )
            for position, t in enumerate(e.todos):
                todo_id = conn.execute(
                    "INSERT INTO notes_todos (entry_id, position, text, size, done_on)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        entry_id,
                        position,
                        t.text,
                        t.size,
                        t.done_on.isoformat() if t.done_on else None,
                    ),
                ).lastrowid
                conn.executemany(
                    "INSERT OR IGNORE INTO notes_todo_links VALUES (?, ?, ?)",
                    [(todo_id, link.kind, link.ref) for link in t.links],
                )
        for ice in ices:
            ice_id = conn.execute(
                "INSERT INTO notes_ice (title, iced_on, note) VALUES (?, ?, ?)",
                (ice.title, ice.iced_on.isoformat(), ice.note),
            ).lastrowid
            conn.executemany(
                "INSERT OR IGNORE INTO notes_iced (ice_id, position, kind, ref_id, sha, name,"
                " repo_dir) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (ice_id, i, item.kind, item.ref_id, item.sha, item.name, item.repo_dir)
                    for i, item in enumerate(ice.items)
                ],
            )


def iced(branch: str, repo_dir: str = "/src/widgets") -> Ice:
    """A one-branch stack on ice."""
    return Ice(
        id=0,
        title=branch,
        iced_on=NOW.date(),
        items=[
            IcedItem(
                kind="git.branch",
                ref_id=f"{repo_dir}\t{branch}",
                name=branch,
                repo_dir=repo_dir,
                sha="1",
            )
        ],
    )


def local_snapshot(**overrides: object) -> LocalSnapshot:
    fields: dict[str, object] = {
        "dir": "/src/widgets",
        "github_repo": REPO,
        "default_branch": "main",
        "worktrees": [],
        "branches": [],
    }
    fields.update(overrides)
    return LocalSnapshot(repos=[LocalRepo.model_validate(fields)])


def stacks(*branches: StackBranch, dir: str = "/src/widgets") -> GitStackSnapshot:
    return GitStackSnapshot(repos=[StackRepo(dir=dir, branches=list(branches))])


def local_branch(name: str, **overrides: object) -> Branch:
    fields: dict[str, object] = {"name": name, "committed_at": NOW - timedelta(days=1)}
    fields.update(overrides)
    return Branch.model_validate(fields)


def know_jira_projects(db: Database, *keys: str) -> None:
    """jira remembers these projects, as if it had seen a ticket of mine in each, so their keys
    link (see `JiraPlugin.linking_patterns`)."""
    for key in keys:
        db.execute("INSERT OR IGNORE INTO jira_projects VALUES (?)", (key,))


def section[M: BaseModel](config: Config, name: str, model: type[M]) -> M:
    """The plugin's section of `config`, as `model`; fails the test when it isn't there."""
    found = config.section(name, model)
    assert found is not None, f"no [{name}]"
    return found
