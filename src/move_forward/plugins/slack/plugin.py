"""The Slack plugin: review pings, and where they go.

People's Slack ids, teams' channels and the pings sent live in its own tables; `mfd slack
import` reads the first two from the skill's `reviewer-map.md`.
"""

import argparse
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field

from ... import render
from ...asking import Asker, InitSetup
from ...graph import Graph
from ...page import Action, Item, Panel, age
from ...plugin import ActionError, ActionHandler, CliHandler, Context, PageHandler, Plugin
from . import cli, contacts
from .config import SlackConfig
from .models import Ping, TeamChannel
from .pings import Pinger, PingTarget, PrPing
from .repository import MIGRATIONS, SlackRepository
from .setup import EXAMPLE, TEMPLATE, questions

log = logging.getLogger(__name__)

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
    target: str = Field(pattern=r"^(user|team):\S+$")
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
    slack = SlackRepository(ctx.db)
    return Pinger(
        ctx.config,
        ctx.now,
        graph,
        slack.contacts(),
        _team_channels(ctx.config, slack.team_channels()),
        slack.pings(),
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
        f" next ping is a follow-up. <a href='{contacts.CONTACTS_PATH}'>Where pings go</a></p>"
        "</article>"
    )
    return render.page(f"Ping: {ping.title}", "ping", body, COPY_SCRIPT)


class SlackPlugin(Plugin[SlackConfig, BaseModel]):
    name = "slack"
    title = "Slack"
    init_template = TEMPLATE
    init_example = EXAMPLE

    def init_suggested(self) -> bool:
        """Only when asked for: it needs a site (jira) or workspace (slack) to be useful."""
        return False

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    config_model = SlackConfig
    migrations: ClassVar[list[str]] = MIGRATIONS
    cli_help = "see and set where review pings go, or import them from the skill's markdown"

    def panels(
        self, ctx: Context[SlackConfig], snapshot: BaseModel | None, graph: Graph
    ) -> list[Panel]:
        slack = pinger(ctx, graph)
        return [slack.nudges(slack.pings())]

    def prepare_item_actions(
        self, ctx: Context[SlackConfig], graph: Graph
    ) -> Callable[[Item], list[Action]]:
        """A Ping link on any item about one of my open PRs: Bump once a target was pinged,
        muted while the PR isn't in order. The pings are worked out once for the page."""
        pings = pinger(ctx, graph).pings()

        def ping_action(item: Item) -> list[Action]:
            pr = item.subject("pr")
            ping = pings.get(pr.id) if pr else None
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

        return ping_action

    def pages(self) -> dict[str, PageHandler]:
        def ping(
            ctx: Context[SlackConfig], graph: Graph, query: dict[str, list[str]]
        ) -> str | None:
            pr_url = query.get("pr", [""])[0]
            found = pinger(ctx, graph).pings().get(pr_url)
            return ping_page(found, ctx.now) if found else None

        def where_pings_go(
            ctx: Context[SlackConfig], graph: Graph, query: dict[str, list[str]]
        ) -> str | None:
            return contacts.contacts_page(ctx, graph)

        return {PING_PATH: ping, contacts.CONTACTS_PATH: where_pings_go}

    def after_collect(self, ctx: Context[SlackConfig], graph: Graph) -> None:
        """Look up the Slack ids of requested reviewers nothing names one for."""
        found = contacts.look_up_requested(ctx, graph)
        if found:
            log.info("found Slack ids for %s", ", ".join(found))

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        def mark_sent(ctx: Context[SlackConfig], graph: Graph, fields: dict[str, str]) -> str:
            """Record a ping as sent, so the next one to that target is a follow-up."""
            try:
                form = PingForm.model_validate(fields)
            except ValueError as e:
                raise ActionError(f"bad ping: {e}") from e
            SlackRepository(ctx.db).add_ping(
                Ping(
                    pr_url=form.pr_url,
                    target=form.target,
                    kind=form.kind,
                    message=form.message,
                    sent_at=datetime.now(UTC),
                )
            )
            return f"marked the {form.kind} ping to {form.target.partition(':')[2]} as sent"

        return {
            "mark_sent": mark_sent,
            "set_contact": contacts.set_contact_action,
            "set_team_channel": contacts.set_team_channel_action,
        }

    def prune(self, ctx: Context[SlackConfig], graph: Graph) -> list[str]:
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        slack = SlackRepository(ctx.db)
        deleted = slack.delete_pings_before(cutoff)
        retried = slack.forget_misses_before(cutoff)
        notices = [f"deleted {deleted} old ping(s)"] if deleted else []
        return notices + ([f"will look up {retried} person(s) again"] if retried else [])
