"""Shared factories: small, fully populated domain objects that tests tweak."""

from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import BaseModel

from move_forward.config import Config
from move_forward.db import Database
from move_forward.domain import Model
from move_forward.page import Panel
from move_forward.panels import Dashboard
from move_forward.plugins.git.models import Branch, LocalRepo, LocalSnapshot
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
from move_forward.plugins.notes import markdown
from move_forward.plugins.notes.models import Entry, Ice, IcedItem, Size, Todo
from move_forward.plugins.notes.repository import MIGRATIONS as NOTES_MIGRATIONS
from move_forward.plugins.notes.repository import NotesRepository
from move_forward.plugins.slack.models import Contact, Ping, TeamChannel
from move_forward.plugins.slack.pings import PrPing
from move_forward.plugins.slack.plugin import pinger
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.refresh import build_dashboard, stored_graph
from move_forward.registry import enabled, migrate
from move_forward.repositories import SnapshotRepository

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
ME = "me"
REPO = "acme/widgets"


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
        "merge_state": "BLOCKED",
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
    }
    fields.update(overrides)
    return GitHubSnapshot.model_validate(fields)


SLACK_OVERRIDES = (
    "slack",
    "slack_workspace_url",
    "ping_first",
    "ping_follow_up",
    "team_channel",
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
    NotesRepository(db).replace_all(
        cast(list[Entry], o.pop("work_items", [])), cast(list[Ice], o.pop("iced_stacks", [])), []
    )
    reviewers = cast(dict[str, Reviewer], o.pop("users", {}))
    channels = cast(dict[str, TeamChannel], o.pop("teams", {}))
    pings = cast(list[Ping], o.pop("pings", []))
    github: dict[str, object] = {"org": "acme"}
    for key in ("review_checks", "placeholder_labels", "ticket_link_prefix", "stale_review_hours"):
        if key in o:
            github[key] = o.pop(key)
    git: dict[str, object] = {"repos": [], "my_branch_prefixes": o.pop("my_branch_prefixes", [])}
    slack: dict[str, object] = {"workspace_url": o.pop("slack_workspace_url", "")}
    for key in ("ping_first", "ping_follow_up", "team_channel"):
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
    for e in enabled(config, db, NOW):  # kept as a refresh keeps them
        if e.name in snapshots:
            e.plugin.store(e.ctx, snapshots[e.name])
            SnapshotRepository(db).save_fetched(e.name, NOW)
    plugins = enabled(config, db, NOW)
    notes = next(e for e in plugins if e.name == "notes")
    notes.plugin.after_collect(notes.ctx, stored_graph(db, plugins)[0])  # what ice covers
    if derived is not None:
        DerivedRepository(db).replace(derived)
        SnapshotRepository(db).save_fetched("github.derived", NOW)
    PeopleRepository(db).replace_imported({u.login: u.email for u in reviewers.values() if u.email})
    if enable_slack:
        SlackRepository(db).replace_imported(
            [Contact(login=u.login, slack_id=u.slack_id) for u in reviewers.values() if u.slack_id],
            list(channels.values()),
            pings,
        )
    return config, db


def dashboard(**overrides: object) -> Dashboard:
    """The dashboard core builds from what `stored` stores, as `refresh.build_dashboard` does."""
    return build_dashboard(*stored(**overrides), NOW)


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
    """An entry, read from the markdown the skill would write for it: `fields` are its
    `- Key: value` lines, e.g. `**{"Blocked on": ["reviews"]}`."""
    lines = [f"## {title}"]
    lines += [f"- {key}: {value}" for key, values in fields.items() for value in values]
    if todos:
        lines.append("- Left to do:")
        lines += [f"  - [{t.size}] {t.text}" if t.size else f"  - {t.text}" for t in todos]
    [read], _ = markdown.read_work_notes("\n".join(lines))
    return read


def iced(branch: str, repo_dir: str = "/src/widgets") -> Ice:
    """A one-branch stack on ice."""
    return Ice(
        id=0,
        title=branch,
        iced_on=NOW.date(),
        items=[
            IcedItem(
                kind="branch",
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
