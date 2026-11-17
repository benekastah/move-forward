"""The Slack plugin: review pings, and where they go.

People's Slack ids, teams' channels and the pings sent live in its own tables. A Slack id is
set by hand (`mfd slack contact set`) or looked up in Slack by the person's email; a team's
channel is set by hand (`mfd slack team set`).
"""

import argparse
from datetime import datetime, timedelta
from typing import ClassVar, Literal
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from move_forward.api import (
    API_VERSION,
    Action,
    ActionError,
    ActionHandler,
    CliHandler,
    Command,
    Context,
    Graph,
    InputRequest,
    ItemAdditions,
    ItemAdditionsFor,
    ItemRequest,
    Migration,
    PageHandler,
    Panel,
    Plugin,
    Request,
    age,
    html,
)
from move_forward.api.kinds import PULL_REQUEST as PR
from move_forward.plugins.slack import cli, contacts
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.models import Ping, TeamChannel
from move_forward.plugins.slack.pings import Pinger, PingTarget, PrPing
from move_forward.plugins.slack.repository import MIGRATIONS, SlackRepository
from move_forward.plugins.slack.setup import INIT

PING = "ping"
"""The ping page for one of my PRs, `/slack/ping?pr=<PR URL>`."""
PING_PATH = f"/slack/{PING}"
COPY_SCRIPT = html.Html(
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
    """`user:<login>` or `team:<id>`, as the ping targets name them."""
    kind: Literal["first", "follow-up"]
    message: str = ""


def _team_channels(config: SlackConfig, known: dict[str, TeamChannel]) -> dict[str, TeamChannel]:
    """The team channels set by hand, with `[slack.teams]` winning, by team id."""
    teams = dict(known)
    for team, channel in config.teams.items():
        current = teams.get(team)
        # The id set by hand is its channel's: it's kept only for the same channel.
        same = current is not None and current.channel_name == channel
        teams[team] = TeamChannel(
            team=team,
            channel_name=channel,
            channel_id=current.channel_id if same and current else None,
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


def _target_form(ping: PrPing, target: PingTarget, now: datetime) -> html.Html:
    last = f" · last {age(now, target.last_ping)} ago" if target.last_ping else ""
    open_link = (
        t"<a class='button' href='{target.slack_link}' target='_blank' rel='noopener'>"
        t"Open Slack</a>"
        if html.linkable(target.slack_link)
        else t"<span class='target-note'>no Slack link known for this target</span>"
    )
    back = urlencode({"back": f"{PING_PATH}?{urlencode({'pr': ping.pr_url})}"})
    return html.html(
        t"<section class='target'><form method='post' action='/actions/slack/mark_sent?{back}'>"
        t"<input type='hidden' name='pr_url' value='{ping.pr_url}'>"
        t"<input type='hidden' name='target' value='{target.kind}:{target.name}'>"
        t"<input type='hidden' name='kind' value='{target.ping_kind}'>"
        t"<div class='target-head'><strong>{target.destination}</strong>"
        t"<span class='target-kind'>{target.ping_kind}{last}</span></div>"
        t"<textarea name='message' rows='2'>{target.message}</textarea>"
        t"<div class='target-buttons'>{open_link}"
        t"<button type='button' class='secondary' onclick='copyMessage(this)'>Copy</button>"
        t"<button type='submit' class='secondary'>Mark as sent</button></div>"
        t"</form></section>"
    )


def ping_page(ping: PrPing, now: datetime, notice: str = "") -> html.Html:
    """The ping page for one of my PRs, with what the last button said (`notice`)."""
    blockers = (
        t"<ul class='blockers'>{[t'<li>{b}</li>' for b in ping.blockers]}</ul>"
        if ping.blockers
        else ""
    )
    targets = [
        _target_form(ping, t, now) for t in ping.targets
    ] or t"<p>No reviewers are requested on this PR.</p>"
    said = t"<p class='notice'>{notice}</p>" if notice else ""
    body = html.html(
        t"<article class='panel padded'>{said}<h2>{ping.title}</h2>"
        t"<p><a href='{ping.pr_url}' target='_blank' rel='noopener'>{ping.pr_url}</a></p>"
        t"{blockers}{targets}"
        t"<p class='target-note'>Copy the message, paste it in Slack, then mark it as sent so the"
        t" next ping is a follow-up. <a href='{contacts.CONTACTS_PATH}'>Where pings go</a></p>"
        t"</article>"
    )
    return html.page(f"Ping: {ping.title}", "ping", body, COPY_SCRIPT)


class SlackPlugin(Plugin[SlackConfig, BaseModel], Command[SlackConfig]):
    name = "slack"
    title = "Slack"
    init = INIT
    config_model = SlackConfig
    api_version = API_VERSION
    migrations: ClassVar[list[Migration]] = MIGRATIONS

    def panels(self, req: Request[SlackConfig, BaseModel]) -> list[Panel]:
        ctx, graph = req.ctx, req.graph
        slack = pinger(ctx, graph)
        return [slack.nudges(slack.pings())]

    def item_actions(self, req: Request[SlackConfig, BaseModel]) -> ItemAdditionsFor:
        """A Ping link on any item about one of my open PRs with someone to chase: Bump once a
        target was pinged, muted while the PR isn't in order. The pings are worked out once for
        the page."""
        pings = pinger(req.ctx, req.graph).pings()

        def ping_action(each: ItemRequest) -> ItemAdditions:
            pr = each.item.subject(PR)
            ping = pings.get(pr.id) if pr else None
            if ping is None or not ping.has_someone_to_chase:
                return ItemAdditions()
            blocked = bool(ping.blockers)
            follow_up = any(t.ping_kind == "follow-up" for t in ping.targets)
            ping_link = [
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
            return ItemAdditions(actions=ping_link)

        return ping_action

    def pages(self) -> dict[str, PageHandler]:
        def ping(req: InputRequest[SlackConfig]) -> html.Html | None:
            ctx, graph, query = req.ctx, req.graph, req.page.query
            pr_url = query.get("pr", [""])[0]
            found = pinger(ctx, graph).pings().get(pr_url)
            notice = req.page.query.get("notice", [""])[0]
            return ping_page(found, ctx.now, notice) if found else None

        def where_pings_go(req: InputRequest[SlackConfig]) -> html.Html | None:
            ctx, graph = req.ctx, req.graph
            return contacts.contacts_page(ctx, graph)

        return {
            PING: ping,
            contacts.CONTACTS: where_pings_go,
        }

    def after_collect(self, req: Request[SlackConfig, BaseModel]) -> None:
        """Look up the Slack ids of requested reviewers nothing names one for."""
        ctx, graph = req.ctx, req.graph
        found = contacts.look_up_requested(ctx, graph)
        if found:
            ctx.log.info("found Slack ids for %s", ", ".join(found))

    def cli(self, parser: argparse.ArgumentParser) -> CliHandler:
        """see and set where review pings go"""
        return cli.add_commands(parser)

    def actions(self) -> dict[str, ActionHandler]:
        def mark_sent(req: InputRequest[SlackConfig]) -> str:
            """Record a ping as sent, so the next one to that target is a follow-up."""
            ctx, fields = req.ctx, req.form.fields
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
                    sent_at=ctx.now,
                )
            )
            return f"marked the {form.kind} ping to {form.target.partition(':')[2]} as sent"

        return {
            "mark_sent": mark_sent,
            "set_contact": contacts.set_contact_action,
            "set_team_channel": contacts.set_team_channel_action,
            "clear_team_channel": contacts.clear_team_channel_action,
            "clear_contact": contacts.clear_contact_action,
        }

    def prune(self, req: Request[SlackConfig, BaseModel]) -> list[str]:
        ctx = req.ctx
        cutoff = ctx.now - timedelta(days=ctx.prune_days)
        slack = SlackRepository(ctx.db)
        deleted = slack.delete_pings_before(cutoff)
        retried = slack.forget_misses_before(cutoff)
        notices = [f"deleted {deleted} old ping(s)"] if deleted else []
        return notices + ([f"will look up {retried} person(s) again"] if retried else [])
