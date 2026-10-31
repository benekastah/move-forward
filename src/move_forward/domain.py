"""The dashboard's own data model: what collectors store and what panels read.

Collectors turn external responses into these models; they are serialized to SQLite as JSON.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    """Base for every model: immutable, and unknown keys are dropped rather than rejected."""

    model_config = ConfigDict(extra="ignore", frozen=True)


# GitHub


class Check(Model):
    name: str
    state: str
    """A CheckRun conclusion (PENDING while running) or a StatusContext state."""
    url: str | None = None


class Thread(Model):
    """A review thread, described by its first comment."""

    resolved: bool
    outdated: bool
    path: str | None = None
    author: str | None = None
    body: str = ""
    url: str | None = None
    created_at: datetime | None = None


class Review(Model):
    author: str | None = None
    state: str
    submitted_at: datetime | None = None


class TimelineEvent(Model):
    type: str
    """The GraphQL type, e.g. ReviewRequestedEvent or RemovedFromMergeQueueEvent."""
    at: datetime
    actor: str | None = None
    """Who reviewed or commented, for PullRequestReview and IssueComment events."""
    reviewer_kind: Literal["user", "team"] | None = None
    reviewer: str | None = None
    """Login or team slug, for ReviewRequestedEvent."""
    reason: str | None = None
    """Why it left the merge queue, for RemovedFromMergeQueueEvent."""


class PullRequest(Model):
    url: str
    number: int
    title: str
    repo: str
    """owner/name."""
    default_branch: str | None = None
    is_draft: bool
    state: str
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None = None
    body: str = ""
    head: str
    head_oid: str
    base: str
    author: str | None = None
    size: int
    """Lines added plus deleted."""
    changed_files: int
    labels: list[str] = []
    mergeable: str
    merge_state: str
    review_decision: str | None = None
    in_merge_queue: bool = False
    requested_users: list[str] = []
    requested_teams: list[str] = []
    reviews: list[Review] = []
    """Each reviewer's latest review."""
    threads: list[Thread] = []
    rollup_state: str | None = None
    checks: list[Check] = []
    last_commit_at: datetime | None = None
    events: list[TimelineEvent] = []
    files: list[str] = []
    """Changed paths; only fetched for searches that need them."""


class GitHubSnapshot(Model):
    login: str
    mine: list[PullRequest]
    """My open PRs."""
    direct_requests: list[PullRequest]
    """Open PRs requesting my review as a user (not through a team)."""
    reviewed_by_me: list[PullRequest]
    """Open PRs by others that I reviewed before."""
    team_requests: list[PullRequest]
    """Open PRs by others requesting my team's review."""
    merged_recent: list[PullRequest]
    """My PRs merged within `history_days`."""
    closed_recent: list[PullRequest] = []
    """My PRs closed without merging within `history_days`."""
    team_members: list[str]


# Local git


class Worktree(Model):
    path: str
    branch: str | None = None
    """None for a detached HEAD."""
    modified: int
    """Changed tracked files, staged or not."""
    untracked: int


class Branch(Model):
    name: str
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    gone: bool = False
    """The upstream was deleted."""
    committed_at: datetime
    ahead_of_default: int | None = None
    """Commits not on the default branch; only computed for recent branches with no upstream."""


class LocalRepo(Model):
    dir: str
    github_repo: str | None = None
    """owner/name of the origin remote, if it is on GitHub."""
    default_branch: str
    worktrees: list[Worktree]
    branches: list[Branch]
    my_dirs: list[str] = []
    """Directories I committed to within `commit_history_days`."""


class LocalSnapshot(Model):
    repos: list[LocalRepo]


# git-stack


class StackBranch(Model):
    """One entry of `git stack info --json`."""

    name: str
    parent: str | None = None
    status: str | None = None
    current: bool = False
    group: str | None = None


class StackRepo(Model):
    dir: str
    branches: list[StackBranch]
    """Every branch git-stack tracks in the repo, and the roots they're on."""


class GitStackSnapshot(Model):
    repos: list[StackRepo]


# Jira


class JiraIssue(Model):
    key: str
    summary: str
    status: str
    status_category: str
    """Jira's category key: new, indeterminate or done."""
    type: str
    labels: list[str] = []
    priority: str | None = None
    created: datetime
    updated: datetime
    parent: str | None = None
    """The parent issue's key, e.g. the epic."""
    open_blockers: list[str] = []
    """Keys of unfinished issues this one "is blocked by"."""
    resolved: datetime | None = None
    """Jira's resolution date, once the ticket is done."""
    url: str


class JiraSnapshot(Model):
    issues: list[JiraIssue]


# Derived from several sources


class ReviewerSuggestion(Model):
    login: str
    commits: int
    """Their commits touching the PR's directories within `commit_history_days`."""


class TeamPrScore(Model):
    owned_files: int
    """Files whose CODEOWNERS owner is my team."""
    required_files: int
    """Of those, files where my team's approval is required."""
    my_files: int
    """Files in directories I committed to recently."""


class DerivedSnapshot(Model):
    suggestions: dict[str, list[ReviewerSuggestion]] = {}
    """Reviewer suggestions keyed by PR URL, for my PRs waiting on my team."""
    team_scores: dict[str, TeamPrScore] = {}
    """Keyed by PR URL, for PRs requesting my team's review."""


# Skill notes


class ReviewerUser(Model):
    login: str
    email: str | None = None
    slack_id: str | None = None


class TeamChannel(Model):
    slug: str
    channel_name: str | None = None
    channel_id: str | None = None


class Ping(Model):
    pr_url: str
    target: str
    kind: Literal["first", "follow-up"]
    message: str = ""
    sent_at: datetime
