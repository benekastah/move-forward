"""Who to ping about each of my open PRs, how, and whether it's time.

Pure functions of my PRs (the graph's `pr` entities), the reviewer map and the pings
recorded so far; no I/O.
"""

from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import Field

from ...domain import Model
from ...graph import Graph, Ref
from ...page import Item, Panel, age
from .config import SlackConfig
from .models import Contact, Ping, TeamChannel

PR = "pr"


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


class PrView(Model):
    """What slack reads of a `pr` (github's)."""

    id: str
    title: str
    url: str | None = None
    state: str | None = None
    repo: str
    number: int
    head: str
    draft: bool
    mine: bool
    created_at: str
    requested_users: list[str] = Field(default_factory=list[str])
    requested_teams: list[str] = Field(default_factory=list[str])
    review_blockers: list[str] | None = None
    review_stale: bool | None = None
    last_review_activity: str | None = None


def my_open_prs(graph: Graph) -> list[PrView]:
    return [pr for pr in graph.view(PR, PrView) if pr.mine and pr.state == "open"]


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
            elif guessed := self.config.guessed_channel(name):
                destination = f"{guessed} (guessed from team_channel)"
            targets.append(
                self._target(
                    "team", name, link, destination, last_by_target.get(f"team:{name}"), pr
                )
            )
        return targets

    def pings(self) -> dict[str, PrPing]:
        """By PR URL, for my open non-draft PRs that aren't on ice."""
        by_pr: dict[str, dict[str, Ping]] = defaultdict(dict)
        any_ping: dict[str, Ping] = {}
        for ping in self.recorded:
            by_pr[ping.pr_url][ping.target] = ping
            any_ping[ping.pr_url] = ping
        result: dict[str, PrPing] = {}
        for pr in self.my_open_prs:
            if pr.draft or self.graph.hidden(Ref(kind=PR, id=pr.id)):  # e.g. on ice
                continue
            url = pr.id
            last_by_target = dict(by_pr.get(url, {}))
            # A nudge the skill logged without a target counts for every target.
            if (untargeted := any_ping.get(url)) and untargeted.target.startswith("unknown"):
                for login in pr.requested_users:
                    last_by_target.setdefault(f"user:{login}", untargeted)
                for name in pr.requested_teams:
                    last_by_target.setdefault(f"team:{name}", untargeted)
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
            if not ping or ping.blockers or not ping.stale:
                continue
            details = [
                f"{t.ping_kind} ping to {t.destination}"
                + (f" (last {age(self.now, t.last_ping)} ago)" if t.last_ping else "")
                for t in ping.targets
            ]
            last = _datetime(pr.last_review_activity)
            details.append(f"last review activity {age(self.now, last)} ago")
            since = last or _datetime(pr.created_at) or self.now
            items.append(
                Item(
                    title=pr.title,
                    url=pr.url,
                    subtitle=f"{pr.repo}#{pr.number}",
                    details=details,
                    rank=-since.timestamp(),
                    subjects=[Ref(kind=PR, id=pr.id)],
                )
            )
        return Panel(
            key="nudges",
            title="Nudge reviewers",
            description="In order (green, no conflicts, no open threads) and quiet for a day.",
            items=items,
        )
