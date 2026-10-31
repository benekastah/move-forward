"""The Slack plugin.

The reviewer map (people's Slack ids, teams' channels) and the pings still live in core's
tables, filled from the notes on every refresh, until the notes move into SQLite.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel

from ... import render
from ...domain import Ping, TeamChannel
from ...graph import Graph
from ...page import Action, Item, Panel, age
from ...plugin import ActionError, ActionHandler, Context, PageHandler, Plugin
from ...repositories import PingRepository, ReviewerRepository
from .config import SlackConfig
from .pings import Pinger, PingTarget, PrPing

PING_PATH = "/ping"
COPY_SCRIPT = (
    "function copyMessage(button) {"
    " const text = button.closest('form').querySelector('textarea').value;"
    " navigator.clipboard.writeText(text).then(() => { button.textContent = 'Copied';"
    " setTimeout(() => { button.textContent = 'Copy'; }, 1500); }); }"
)
"""The ping page's copy button."""


class PingForm(BaseModel):
    """Fields of the "mark as sent" form on the ping page."""

    pr_url: str
    target: str
    """`user:<login>` or `team:<name>`, as the ping targets name them."""
    kind: Literal["first", "follow-up"]
    message: str = ""


def _team_channels(config: SlackConfig, known: dict[str, TeamChannel]) -> dict[str, TeamChannel]:
    """The reviewer map's team channels, with `[slack.teams]` winning by name."""
    teams = dict(known)
    for name, channel in config.teams.items():
        current = teams.get(name)
        teams[name] = TeamChannel(
            slug=name, channel_name=channel, channel_id=current.channel_id if current else None
        )
    return teams


def pinger(ctx: Context[SlackConfig], graph: Graph) -> Pinger:
    reviewers = ReviewerRepository(ctx.db)
    return Pinger(
        ctx.config,
        ctx.now,
        graph,
        reviewers.users(),
        _team_channels(ctx.config, reviewers.teams()),
        PingRepository(ctx.db).all(),
    )


def _target_form(ping: PrPing, target: PingTarget, now: datetime) -> str:
    e = render.e
    last = f" · last {age(now, target.last_ping)} ago" if target.last_ping else ""
    open_link = (
        f"<a class='button' href='{e(target.slack_link)}' target='_blank' rel='noopener'>"
        "Open Slack</a>"
        if target.slack_link
        else "<span class='target-note'>no Slack link known for this target</span>"
    )
    return (
        "<section class='target'><form method='post' action='/actions/slack/mark_sent'>"
        f"<input type='hidden' name='pr_url' value='{e(ping.pr_url)}'>"
        f"<input type='hidden' name='target' value='{e(target.kind)}:{e(target.name)}'>"
        f"<input type='hidden' name='kind' value='{e(target.ping_kind)}'>"
        f"<div class='target-head'><strong>{e(target.destination)}</strong>"
        f"<span class='target-kind'>{e(target.ping_kind)}{e(last)}</span></div>"
        f"<textarea name='message' rows='2'>{e(target.message)}</textarea>"
        f"<div class='target-buttons'>{open_link}"
        "<button type='button' class='secondary' onclick='copyMessage(this)'>Copy</button>"
        "<button type='submit' class='secondary'>Mark as sent</button></div>"
        "</form></section>"
    )


def ping_page(ping: PrPing, now: datetime) -> str:
    e = render.e
    blockers = (
        "<ul class='blockers'>" + "".join(f"<li>{e(b)}</li>" for b in ping.blockers) + "</ul>"
        if ping.blockers
        else ""
    )
    targets = "".join(_target_form(ping, t, now) for t in ping.targets)
    if not targets:
        targets = "<p>No reviewers are requested on this PR.</p>"
    body = (
        "<article class='panel padded'>"
        f"<h2>{e(ping.title)}</h2>"
        f"<p><a href='{e(ping.pr_url)}' target='_blank' rel='noopener'>{e(ping.pr_url)}</a></p>"
        f"{blockers}{targets}"
        "<p class='target-note'>Copy the message, paste it in Slack, then mark it as sent so the"
        " next ping is a follow-up.</p></article>"
    )
    return render.page(f"Ping: {ping.title}", "ping", body, COPY_SCRIPT)


class SlackPlugin(Plugin[SlackConfig, BaseModel]):
    name = "slack"
    title = "Slack"
    config_model = SlackConfig

    def panels(
        self, ctx: Context[SlackConfig], snapshot: BaseModel | None, graph: Graph
    ) -> list[Panel]:
        slack = pinger(ctx, graph)
        return [slack.nudges(slack.pings())]

    def item_actions(self, ctx: Context[SlackConfig], item: Item, graph: Graph) -> list[Action]:
        """A Ping link on any item about one of my open PRs: Bump once a target was pinged,
        muted while the PR isn't in order."""
        if item.subject is None or item.subject.kind != "pr":
            return []
        ping = pinger(ctx, graph).pings().get(item.subject.id)
        if ping is None:
            return []
        blocked = bool(ping.blockers)
        follow_up = any(t.ping_kind == "follow-up" for t in ping.targets)
        return [
            Action(
                name="",
                label="Ping…" if blocked else ("Bump" if follow_up else "Ping"),
                fields={},
                href=f"{PING_PATH}?pr={quote(ping.pr_url, safe='')}",
                title="Not in order: " + ", ".join(ping.blockers)
                if blocked
                else "Send review pings",
                muted=blocked,
            )
        ]

    def pages(self) -> dict[str, PageHandler]:
        def ping(
            ctx: Context[SlackConfig], graph: Graph, query: dict[str, list[str]]
        ) -> str | None:
            pr_url = query.get("pr", [""])[0]
            found = pinger(ctx, graph).pings().get(pr_url)
            return ping_page(found, ctx.now) if found else None

        return {PING_PATH: ping}

    def actions(self) -> dict[str, ActionHandler]:
        def mark_sent(ctx: Context[SlackConfig], graph: Graph, fields: dict[str, str]) -> str:
            """Record a ping as sent, so the next one to that target is a follow-up."""
            try:
                form = PingForm.model_validate(fields)
            except ValueError as e:
                raise ActionError(f"bad ping: {e}") from e
            PingRepository(ctx.db).add(
                Ping(
                    pr_url=form.pr_url,
                    target=form.target,
                    kind=form.kind,
                    message=form.message,
                    sent_at=datetime.now(UTC),
                )
            )
            return f"marked the {form.kind} ping to {form.target.partition(':')[2]} as sent"

        return {"mark_sent": mark_sent}

    def prune(self, ctx: Context[SlackConfig], graph: Graph) -> list[str]:
        deleted = PingRepository(ctx.db).delete_before(ctx.now - timedelta(days=ctx.prune_days))
        return [f"deleted {deleted} old ping(s)"] if deleted else []
