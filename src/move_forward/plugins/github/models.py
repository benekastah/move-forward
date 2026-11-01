"""The github plugin's data: PRs as its searches find them, and what it works out from them."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from ...domain import Model


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
    labels: list[str] = Field(default_factory=list)
    mergeable: str
    merge_state: str
    review_decision: str | None = None
    in_merge_queue: bool = False
    requested_users: list[str] = Field(default_factory=list)
    requested_teams: list[str] = Field(default_factory=list)
    reviews: list[Review] = Field(default_factory=list)
    """Each reviewer's latest review."""
    threads: list[Thread] = Field(default_factory=list)
    rollup_state: str | None = None
    checks: list[Check] = Field(default_factory=list)
    last_commit_at: datetime | None = None
    events: list[TimelineEvent] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
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
    closed_recent: list[PullRequest] = Field(default_factory=list)
    """My PRs closed without merging within `history_days`."""
    team_members: list[str]


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
    suggestions: dict[str, list[ReviewerSuggestion]] = Field(default_factory=dict)
    """Reviewer suggestions keyed by PR URL, for my PRs waiting on my team."""
    team_scores: dict[str, TeamPrScore] = Field(default_factory=dict)
    """Keyed by PR URL, for PRs requesting my team's review."""
