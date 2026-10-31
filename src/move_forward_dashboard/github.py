"""GitHub collector: PR searches through `gh api graphql`, converted into domain models.

The `Raw*` models mirror the GraphQL response shape, dropping anything we didn't ask for.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain import (
    Check,
    GitHubSnapshot,
    PullRequest,
    Review,
    Thread,
    TimelineEvent,
)
from .shell import CommandError, run, stopping

COMMIT_EMAIL_LOOKUP_COUNT = 10
PAGE_SIZE = 25
"""PRs per search page. Each carries threads, checks and events, and large pages time out."""
PAGE_ATTEMPTS = 2
PARALLEL_REQUESTS = 8
"""The searches and lookups `collect` runs at once; well under gh's rate limits."""
"""GitHub sometimes answers a heavy search with an empty body; one retry usually fixes it."""


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Named(_Raw):
    login: str | None = None
    slug: str | None = None
    typename: str | None = Field(default=None, alias="__typename")


class _Nodes[T](_Raw):
    nodes: list[T | None] = []


class _Label(_Raw):
    name: str


class _ReviewRequest(_Raw):
    requested_reviewer: _Named | None = Field(default=None, alias="requestedReviewer")


class _RawReview(_Raw):
    author: _Named | None = None
    state: str
    submitted_at: datetime | None = Field(default=None, alias="submittedAt")


class _RawComment(_Raw):
    author: _Named | None = None
    body: str = ""
    url: str | None = None
    created_at: datetime | None = Field(default=None, alias="createdAt")


class _RawThread(_Raw):
    is_resolved: bool = Field(alias="isResolved")
    is_outdated: bool = Field(alias="isOutdated")
    path: str | None = None
    comments: _Nodes[_RawComment] = _Nodes()


class _RawContext(_Raw):
    typename: str = Field(alias="__typename")
    name: str | None = None
    conclusion: str | None = None
    status: str | None = None
    details_url: str | None = Field(default=None, alias="detailsUrl")
    context: str | None = None
    state: str | None = None
    target_url: str | None = Field(default=None, alias="targetUrl")


class _RawRollup(_Raw):
    state: str | None = None
    contexts: _Nodes[_RawContext] = _Nodes()


class _RawCommit(_Raw):
    committed_date: datetime | None = Field(default=None, alias="committedDate")
    status_check_rollup: _RawRollup | None = Field(default=None, alias="statusCheckRollup")


class _RawCommitNode(_Raw):
    commit: _RawCommit


class _RawEvent(_Raw):
    typename: str = Field(alias="__typename")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    requested_reviewer: _Named | None = Field(default=None, alias="requestedReviewer")
    reason: str | None = None
    author: _Named | None = None


class _RawFile(_Raw):
    path: str


class _RawRef(_Raw):
    name: str


class _RawRepository(_Raw):
    name_with_owner: str = Field(alias="nameWithOwner")
    default_branch_ref: _RawRef | None = Field(default=None, alias="defaultBranchRef")


class RawPullRequest(_Raw):
    """A PullRequest node of `search_prs.graphql`."""

    number: int
    title: str
    url: str
    is_draft: bool = Field(alias="isDraft")
    state: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    merged_at: datetime | None = Field(default=None, alias="mergedAt")
    body: str | None = None
    head_ref_name: str = Field(alias="headRefName")
    head_ref_oid: str = Field(alias="headRefOid")
    base_ref_name: str = Field(alias="baseRefName")
    additions: int = 0
    deletions: int = 0
    changed_files: int = Field(default=0, alias="changedFiles")
    is_in_merge_queue: bool = Field(default=False, alias="isInMergeQueue")
    mergeable: str = "UNKNOWN"
    merge_state_status: str = Field(default="UNKNOWN", alias="mergeStateStatus")
    review_decision: str | None = Field(default=None, alias="reviewDecision")
    author: _Named | None = None
    repository: _RawRepository
    labels: _Nodes[_Label] = _Nodes()
    review_requests: _Nodes[_ReviewRequest] = Field(default=_Nodes(), alias="reviewRequests")
    latest_reviews: _Nodes[_RawReview] = Field(default=_Nodes(), alias="latestReviews")
    review_threads: _Nodes[_RawThread] = Field(default=_Nodes(), alias="reviewThreads")
    commits: _Nodes[_RawCommitNode] = _Nodes()
    timeline_items: _Nodes[_RawEvent] = Field(default=_Nodes(), alias="timelineItems")
    files: _Nodes[_RawFile] | None = None


class _PageInfo(_Raw):
    has_next_page: bool = Field(alias="hasNextPage")
    end_cursor: str | None = Field(default=None, alias="endCursor")


class _RawSearch(_Raw):
    issue_count: int = Field(alias="issueCount")
    page_info: _PageInfo = Field(alias="pageInfo")
    nodes: list[RawPullRequest | None] = []


class _RawData(_Raw):
    search: _RawSearch


class RawSearchResponse(_Raw):
    """The whole `gh api graphql` response."""

    data: _RawData


QUERY_PATH = Path(__file__).parent / "queries" / "search_prs.graphql"


def _query_text() -> str:
    return QUERY_PATH.read_text()


def _run_with_retry(args: list[str]) -> str:
    for attempt in range(1, PAGE_ATTEMPTS + 1):
        try:
            return run(args)
        except CommandError:
            if attempt == PAGE_ATTEMPTS or stopping():
                raise
    raise AssertionError("unreachable")


def search_prs(query: str, with_files: bool = False) -> list[PullRequest]:
    """Run a PR search, following pagination, and return every PR it finds."""
    prs: list[PullRequest] = []
    after: str | None = None
    while True:
        args = [
            "gh",
            "api",
            "graphql",
            "-F",
            f"withFiles={'true' if with_files else 'false'}",
            "-F",
            f"first={PAGE_SIZE}",
            "-f",
            f"q={query}",
            "-f",
            f"query={_query_text()}",
        ]
        if after:
            args += ["-f", f"after={after}"]
        response = RawSearchResponse.model_validate_json(_run_with_retry(args))
        prs.extend(to_pull_request(node) for node in response.data.search.nodes if node)
        page = response.data.search.page_info
        if not page.has_next_page or not page.end_cursor:
            return prs
        after = page.end_cursor


def _check(context: _RawContext) -> Check:
    if context.typename == "CheckRun":
        state = context.conclusion if context.status == "COMPLETED" else None
        return Check(name=context.name or "", state=state or "PENDING", url=context.details_url)
    return Check(
        name=context.context or "", state=context.state or "PENDING", url=context.target_url
    )


def _reviewer(named: _Named | None) -> tuple[Literal["user", "team"], str] | None:
    """('user', login) or ('team', slug); None for reviewers we can't see (e.g. bots)."""
    if named is None:
        return None
    if named.typename == "User" and named.login:
        return ("user", named.login)
    if named.typename == "Team" and named.slug:
        return ("team", named.slug)
    return None


def _event(raw: _RawEvent) -> TimelineEvent | None:
    if raw.created_at is None:
        return None
    reviewer = _reviewer(raw.requested_reviewer)
    return TimelineEvent(
        type=raw.typename,
        at=raw.created_at,
        actor=raw.author.login if raw.author else None,
        reviewer_kind=reviewer[0] if reviewer else None,
        reviewer=reviewer[1] if reviewer else None,
        reason=raw.reason,
    )


def _thread(raw: _RawThread) -> Thread:
    first = next((c for c in raw.comments.nodes if c), None)
    return Thread(
        resolved=raw.is_resolved,
        outdated=raw.is_outdated,
        path=raw.path,
        author=first.author.login if first and first.author else None,
        body=first.body if first else "",
        url=first.url if first else None,
        created_at=first.created_at if first else None,
    )


def to_pull_request(raw: RawPullRequest) -> PullRequest:
    """Flatten a GraphQL PullRequest node into the domain model."""
    requested = [_reviewer(r.requested_reviewer) for r in raw.review_requests.nodes if r]
    commit = next((c.commit for c in raw.commits.nodes if c), None)
    rollup = commit.status_check_rollup if commit else None
    return PullRequest(
        url=raw.url,
        number=raw.number,
        title=raw.title,
        repo=raw.repository.name_with_owner,
        default_branch=raw.repository.default_branch_ref.name
        if raw.repository.default_branch_ref
        else None,
        is_draft=raw.is_draft,
        state=raw.state,
        created_at=raw.created_at,
        updated_at=raw.updated_at,
        merged_at=raw.merged_at,
        body=raw.body or "",
        head=raw.head_ref_name,
        head_oid=raw.head_ref_oid,
        base=raw.base_ref_name,
        author=raw.author.login if raw.author else None,
        size=raw.additions + raw.deletions,
        changed_files=raw.changed_files,
        labels=[label.name for label in raw.labels.nodes if label],
        mergeable=raw.mergeable,
        merge_state=raw.merge_state_status,
        review_decision=raw.review_decision,
        in_merge_queue=raw.is_in_merge_queue,
        requested_users=[name for kind, name in filter(None, requested) if kind == "user"],
        requested_teams=[name for kind, name in filter(None, requested) if kind == "team"],
        reviews=[
            Review(
                author=r.author.login if r.author else None,
                state=r.state,
                submitted_at=r.submitted_at,
            )
            for r in raw.latest_reviews.nodes
            if r
        ],
        threads=[_thread(t) for t in raw.review_threads.nodes if t],
        rollup_state=rollup.state if rollup else None,
        checks=[_check(c) for c in rollup.contexts.nodes if c] if rollup else [],
        last_commit_at=commit.committed_date if commit else None,
        events=[
            e
            for e in (_event(raw_event) for raw_event in raw.timeline_items.nodes if raw_event)
            if e
        ],
        files=[f.path for f in raw.files.nodes if f] if raw.files else [],
    )


def login() -> str:
    return run(["gh", "api", "user", "--jq", ".login"]).strip()


def team_members(org: str, team: str) -> list[str]:
    out = run(["gh", "api", "--paginate", f"orgs/{org}/teams/{team}/members", "--jq", ".[].login"])
    return out.split()


def commit_email(org_repo: str, user: str, email_domains: list[str]) -> str | None:
    """A user's commit email in one of `email_domains`, from their recent commits in `org_repo`."""
    out = run(
        [
            "gh",
            "api",
            f"repos/{org_repo}/commits?author={user}&per_page={COMMIT_EMAIL_LOOKUP_COUNT}",
            "--jq",
            ".[].commit.author.email",
        ]
    )
    return next((e for e in out.split() if e.rsplit("@", 1)[-1] in email_domains), None)


class _RawPrState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    state: str
    merged_at: datetime | None = Field(default=None, alias="mergedAt")
    closed_at: datetime | None = Field(default=None, alias="closedAt")


def pr_closed_at(url: str) -> datetime | None:
    """When a PR was merged or closed, or None while it's open."""
    raw = _RawPrState.model_validate_json(
        run(["gh", "pr", "view", url, "--json", "state,mergedAt,closedAt"])
    )
    if raw.state == "OPEN":
        return None
    return raw.merged_at or raw.closed_at


def collect(org: str, teams: list[str], history_days: int, now: datetime) -> GitHubSnapshot:
    """Every PR search the panels need."""
    since = (now - timedelta(days=history_days)).date().isoformat()
    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
        login_future = pool.submit(login)
        mine = pool.submit(search_prs, "is:pr is:open author:@me archived:false", True)
        direct = pool.submit(search_prs, "is:pr is:open user-review-requested:@me archived:false")
        reviewed = pool.submit(
            search_prs, "is:pr is:open reviewed-by:@me -author:@me archived:false"
        )
        team_futures = [
            pool.submit(
                search_prs,
                f"is:pr is:open team-review-requested:{org}/{team} -author:@me archived:false",
                True,
            )
            for team in teams
        ]
        merged = pool.submit(search_prs, f"is:pr is:merged author:@me merged:>={since}")
        closed = pool.submit(search_prs, f"is:pr is:closed is:unmerged author:@me closed:>={since}")
        member_futures = [pool.submit(team_members, org, team) for team in teams]
    team_requests: dict[str, PullRequest] = {}
    for future in team_futures:
        for pr in future.result():
            team_requests.setdefault(pr.url, pr)
    return GitHubSnapshot(
        login=login_future.result(),
        mine=mine.result(),
        direct_requests=direct.result(),
        reviewed_by_me=reviewed.result(),
        team_requests=list(team_requests.values()),
        merged_recent=merged.result(),
        closed_recent=closed.result(),
        team_members=sorted({login for f in member_futures for login in f.result()}),
    )
