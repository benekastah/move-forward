"""The github plugin's data: PRs as its searches find them, and what it works out from them."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from ...api import Model


class Check(Model):
    name: str
    state: str
    """A CheckRun conclusion (PENDING while running) or a StatusContext state."""
    url: str | None = None
    description: str | None = None
    """A status context's explanation, used to distinguish pending reviews from other policy failures."""


class Thread(Model):
    """A review thread, described by its first comment."""

    resolved: bool
    outdated: bool
    path: str | None = None
    author: str | None = None
    body: str = ""
    url: str | None = None
    created_at: datetime | None = None
    comment_count: int = 1
    """Comments in the thread, the first included: a reply raises it."""


class Review(Model):
    author: str | None = None
    state: str
    submitted_at: datetime | None = None


class TimelineEvent(Model):
    type: str
    """The GraphQL type, e.g. ReviewRequestedEvent or RemovedFromMergeQueueEvent."""
    at: datetime
    actor: str | None = None
    """Who reviewed or commented (PullRequestReview, IssueComment), or queued the PR
    (AddedToMergeQueueEvent)."""
    reviewer_kind: Literal["user", "team"] | None = None
    reviewer: str | None = None
    """Login or team slug, for ReviewRequestedEvent."""
    reason: str | None = None
    """Why it left the merge queue, for RemovedFromMergeQueueEvent."""
    state: str | None = None
    """APPROVED, CHANGES_REQUESTED, COMMENTED and so on, for PullRequestReview; None for
    other events and for reviews stored before it was kept."""
    has_body: bool = False
    """A review has text of its own, beyond its inline comments, for PullRequestReview."""


MergeMethod = Literal["MERGE", "SQUASH", "REBASE"]
"""How a PR merges: a merge commit, squashed, or rebased."""


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
    assignees: list[str] = Field(default_factory=list)
    """The first 10; the author among them when a repo assigns its authors."""
    mergeable: str
    merge_state: str
    review_decision: str | None = None
    in_merge_queue: bool = False
    merge_queue: bool = False
    """Its base branch has a merge queue: it merges by joining it."""
    merge_methods: list[MergeMethod] = Field(default_factory=list)
    """How its repo lets it merge; empty while not known yet (kept from before they were
    fetched, until the next full refresh), not as "it can't"."""
    default_merge_method: MergeMethod | None = None
    """The method GitHub picks for me in its repo: my last used, else the repo's default."""
    auto_merge_allowed: bool = False
    """Its repo lets a PR merge by itself once it's ready (auto-merge)."""
    delete_branch_on_merge: bool = False
    """Its repo deletes a PR's branch once it merges."""
    auto_merge: MergeMethod | None = None
    """The method it'll merge by itself with, once ready; None without auto-merge on, or with a
    method GitHub added since (see `client._method`)."""
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
    """Changed paths (the first 100); only fetched for the searches that need them: my PRs and
    the ones I review."""
    file_lines: list[int] = Field(default_factory=list)
    """Lines added plus deleted in each of `files`, in the same order."""


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
    mentioned: list[PullRequest] = Field(default_factory=list)
    """Open PRs by others that mention me, updated within `history_days`."""
    team_members: list[str]
    team_memberships: dict[str, list[str]] = Field(default_factory=dict)
    """`owner/slug` to member logins, for requested teams. A login may be in many lists."""


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
    part_files: int = 0
    """Files owned by me or my teams in CODEOWNERS; 0 when none are (or there is no file)."""
    part_lines: int = 0
    """Lines changed in those files."""


class DerivedSnapshot(Model):
    suggestions: dict[str, list[ReviewerSuggestion]] = Field(default_factory=dict)
    """Reviewer suggestions keyed by PR URL, for my PRs waiting on my team."""
    team_scores: dict[str, TeamPrScore] = Field(default_factory=dict)
    """Keyed by PR URL, for PRs requesting my team's review."""
