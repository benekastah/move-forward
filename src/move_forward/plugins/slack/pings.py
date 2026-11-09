"""Who to ping about each of my open PRs, how, and whether it's time.

Pure functions of my PRs (the graph's `pull_request` entities), where pings go and the pings
recorded so far; no I/O.
"""

from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import Field

from ...api import Band, Graph, Item, Model, Panel, Ref, Signals, age
from ...api.kinds import PULL_REQUEST as PR
from .config import SlackConfig
from .models import Contact, Ping, TeamChannel

ALREADY_APPROVED = "already approved"
"""The `review_blockers` entry of a PR with the approval it needs (see `docs/entities.md`)."""


class PingTarget(Model):
    kind: Literal["user", "team"]
    name: str
    """Login or team name."""
    slack_link: str | None = None
    """Where to paste the message: the channel or the person's profile."""
    destination: str
    """What the link opens, e.g. `#team-web (C0123ABCD)` or `alice (DM)`."""
    ping_kind: Literal["first", "follow-up"]
    last_ping: datetime | None = None
    message: str


class PrPing(Model):
    """Everything the ping page needs for one of my PRs."""

    pr_url: str
    title: str
    targets: list[PingTarget]
    blockers: list[str]
    """Why the skill wouldn't nudge yet; empty when the PR is in order."""
    stale: bool
    """No review activity for `[github] stale_review_hours`."""

    @property
    def has_someone_to_chase(self) -> bool:
        """Whether it asks anyone to review, and still needs their approval."""
        return bool(self.targets) and ALREADY_APPROVED not in self.blockers


class PrView(Model):
    """What slack reads of a `pull_request`."""

    id: str
    title: str
    url: str | None = None
    state: str | None = None
    repo: str
    handle: str
    head: str
    draft: bool
    mine: bool
    created_at: str
    requested_users: list[str] = Field(default_factory=list[str])
    requested_teams: list[str] = Field(default_factory=list[str])
    review_blockers: list[str] | None = None
    review_stale: bool | None = None
    stale_review_hours: int | None = None
    last_review_activity: str | None = None


def my_open_prs(graph: Graph) -> list[PrView]:
    return [pr for pr in graph.view(PR, PrView) if pr.mine and pr.state == "open"]


def _quiet(prs: list[PrView]) -> str:
    """How long a review request must be quiet to be nudged, as github's
    `stale_review_hours` says: "a day", "3 days", "6 hours"."""
    hours = next((pr.stale_review_hours for pr in prs if pr.stale_review_hours), 24)
    if hours % 24 == 0:
        days = hours // 24
        return "a day" if days == 1 else f"{days} days"
    return "an hour" if hours == 1 else f"{hours} hours"


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _slack_link(workspace: str, path: str) -> str | None:
    return f"{workspace.rstrip('/')}/{path}" if workspace else None


class Pinger:
    def __init__(
        self,
        config: SlackConfig,
        now: datetime,
        graph: Graph,
        users: dict[str, Contact],
        teams: dict[str, TeamChannel],
        pings: list[Ping],
    ):
        self.config = config
        self.now = now
        self.graph = graph
        self.users = users
        self.teams = teams
        self.recorded = pings
        self.my_open_prs = my_open_prs(graph)

    def _target(
        self,
        kind: Literal["user", "team"],
        name: str,
        link: str | None,
        destination: str,
        last: Ping | None,
        pr: PrView,
    ) -> PingTarget:
        ping_kind: Literal["first", "follow-up"] = "follow-up" if last else "first"
        return PingTarget(
            kind=kind,
            name=name,
            slack_link=link,
            destination=destination,
            ping_kind=ping_kind,
            last_ping=last.sent_at if last else None,
            message=self.config.ping_first.format(url=pr.id)
            if ping_kind == "first"
            else self.config.ping_follow_up,
        )

    def _targets(self, pr: PrView, last_by_target: dict[str, Ping]) -> list[PingTarget]:
        targets: list[PingTarget] = []
        workspace = self.config.workspace_url
        for login in pr.requested_users:
            user = self.users.get(login)
            link = (
                _slack_link(workspace, f"team/{user.slack_id}") if user and user.slack_id else None
            )
            targets.append(
                self._target(
                    "user", login, link, f"{login} (DM)", last_by_target.get(f"user:{login}"), pr
                )
            )
        for name in pr.requested_teams:
            team = self.teams.get(name)
            link = None
            destination = f"team {name}"
            if team and team.channel_id:
                link = _slack_link(workspace, f"archives/{team.channel_id}")
                destination = f"{team.channel_name or name} ({team.channel_id})"
            elif team and team.channel_name:
                destination = team.channel_name
            targets.append(
                self._target(
                    "team", name, link, destination, last_by_target.get(f"team:{name}"), pr
                )
            )
        return targets

    def pings(self) -> dict[str, PrPing]:
        """By PR URL, for my open non-draft PRs that aren't on ice."""
        by_pr: dict[str, dict[str, Ping]] = defaultdict(dict)
        untargeted: dict[str, Ping] = {}  # the latest nudge the skill logged without a target
        for ping in self.recorded:
            by_pr[ping.pr_url][ping.target] = ping
            if ping.target.startswith("unknown"):
                untargeted[ping.pr_url] = ping
        result: dict[str, PrPing] = {}
        for pr in self.my_open_prs:
            if pr.draft or self.graph.hidden(Ref(kind=PR, id=pr.id)):  # e.g. on ice
                continue
            url = pr.id
            last_by_target = dict(by_pr.get(url, {}))
            # A nudge the skill logged without a target counts for every target, as its last
            # ping unless one of its own came after.
            if nudge := untargeted.get(url):
                keys = [f"user:{u}" for u in pr.requested_users]
                keys += [f"team:{t}" for t in pr.requested_teams]
                for key in keys:
                    own = last_by_target.get(key)
                    if own is None or own.sent_at < nudge.sent_at:
                        last_by_target[key] = nudge
            result[url] = PrPing(
                pr_url=url,
                title=pr.title,
                targets=self._targets(pr, last_by_target),
                blockers=pr.review_blockers or [],
                stale=bool(pr.review_stale),
            )
        return result

    def nudges(self, pings: dict[str, PrPing]) -> Panel:
        items: list[Item] = []
        for pr in self.my_open_prs:
            ping = pings.get(pr.id)
            if not ping or not ping.has_someone_to_chase or ping.blockers or not ping.stale:
                continue
            details = [
                f"{t.ping_kind} ping to {t.destination}"
                + (f" (last {age(self.now, t.last_ping)} ago)" if t.last_ping else "")
                for t in ping.targets
            ]
            last = _datetime(pr.last_review_activity)
            details.append(f"last review activity {age(self.now, last)} ago")
            since = last or _datetime(pr.created_at) or self.now
            follow_up = any(t.ping_kind == "follow-up" for t in ping.targets)
            items.append(
                Item(
                    title=pr.title,
                    url=pr.url,
                    subtitle=f"{pr.repo}{pr.handle}",
                    details=details,
                    rank=-since.timestamp(),
                    subjects=[Ref(kind=PR, id=pr.id)],
                    signals=Signals(
                        verb="Bump the review" if follow_up else "Ping the reviewers",
                        task="ping",
                        effort="minutes",
                    ),
                )
            )
        return Panel(
            key="nudges",
            priority=Band.WAITING,
            act_now=True,
            title="Nudge reviewers",
            description="In order (green, no conflicts, no open threads), with a reviewer asked"
            f" and the PR not yet approved, and quiet for {_quiet(self.my_open_prs)}.",
            items=items,
        )
