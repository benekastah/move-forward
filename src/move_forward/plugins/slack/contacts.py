"""Where review pings go, viewed and edited: the `/slack/contacts` page, its buttons, and
what `mfd slack` shares with them."""

import re

from ... import render
from ...domain import Model
from ...graph import Graph
from ...plugin import ActionError, Context
from . import client
from .config import SlackConfig
from .pings import my_open_prs
from .repository import DASHBOARD, IMPORTED, LOOKED_UP, SlackRepository

CONTACTS_PATH = "/slack/contacts"
PR = "pr"
PERSON = "person"
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")
CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{6,}$")
CHANNEL_NAME_RE = re.compile(r"^#[\w-]+$")


class _PersonView(Model):
    """What slack reads of a `person` (github's): their commit email."""

    email: str | None = None


def _optional(value: str, pattern: re.Pattern[str], what: str) -> str | None:
    """`value` stripped, or None when blank; raises unless it matches `pattern`."""
    value = value.strip()
    if not value:
        return None
    if not pattern.match(value):
        raise ActionError(f"{value!r} isn't a {what}")
    return value


def set_contact(slack: SlackRepository, login: str, slack_id: str) -> str:
    login = login.strip()
    if not login:
        raise ActionError("a GitHub login is needed")
    slack.set_contact(login, _optional(slack_id, SLACK_ID_RE, "Slack member id, e.g. U0123456"))
    return f"{login}'s pings go to {slack_id.strip() or 'no Slack link'}"


def set_team_channel(slack: SlackRepository, team: str, name: str, channel_id: str) -> str:
    team = team.strip()
    if not team:
        raise ActionError("a team is needed")
    channel_name = _optional(name, CHANNEL_NAME_RE, "channel name, e.g. #team-web")
    channel = _optional(channel_id, CHANNEL_ID_RE, "channel id, e.g. C0123456")
    if channel_name is None and channel is None:
        raise ActionError("a channel name or id is needed")
    slack.set_team_channel(team, channel_name, channel)
    return f"{team}'s pings go to {channel_name or channel}"


def set_contact_action(ctx: Context[SlackConfig], graph: Graph, fields: dict[str, str]) -> str:
    return set_contact(SlackRepository(ctx.db), fields["login"], fields.get("slack_id", ""))


def set_team_channel_action(ctx: Context[SlackConfig], graph: Graph, fields: dict[str, str]) -> str:
    return set_team_channel(
        SlackRepository(ctx.db),
        fields["team"],
        fields.get("channel_name", ""),
        fields.get("channel_id", ""),
    )


def _requested(graph: Graph) -> tuple[set[str], set[str]]:
    """The people and teams my open PRs ask to review, by login and team."""
    users: set[str] = set()
    teams: set[str] = set()
    for pr in my_open_prs(graph):
        users.update(pr.requested_users)
        teams.update(pr.requested_teams)
    return users, teams


def _form(action: str, hidden: dict[str, str], inputs: list[tuple[str, str, str]]) -> str:
    e = render.e
    fields = "".join(
        f"<input type='hidden' name='{e(k)}' value='{e(v)}'>" for k, v in hidden.items()
    )
    fields += "".join(
        f"<input type='text' name='{e(name)}' value='{e(value)}' placeholder='{e(hint)}'>"
        for name, value, hint in inputs
    )
    return (
        f"<form method='post' action='/actions/slack/{action}?back={CONTACTS_PATH}'"
        f" class='action'>{fields}<button type='submit' class='secondary small'>Save</button>"
        "</form>"
    )


def contacts_page(ctx: Context[SlackConfig], graph: Graph) -> str:
    """Everyone and every team pings can go to: the ones my open PRs ask for, and the ones
    imported or set, with where their pings go and an edit form each."""
    e = render.e
    slack = SlackRepository(ctx.db)
    contacts, channels, sources = slack.contacts(), slack.team_channels(), slack.sources()
    users, teams = _requested(graph)

    def how(target: str) -> str:
        source = sources.get(target)
        return {DASHBOARD: "set by hand", IMPORTED: "imported", LOOKED_UP: "looked up"}.get(
            source or "", "not known"
        )

    people_rows = "".join(
        f"<tr><td>{e(login)}</td><td>{e(how(f'user:{login}'))}</td><td>"
        + _form(
            "set_contact",
            {"login": login},
            [("slack_id", contacts[login].slack_id or "" if login in contacts else "", "U0123456")],
        )
        + "</td></tr>"
        for login in sorted(users | set(contacts))
    )
    team_rows = ""
    for team in sorted(teams | set(channels) | set(ctx.config.teams)):
        channel = channels.get(team)
        configured = ctx.config.teams.get(team)
        note = f"{how(f'team:{team}')}" + (
            f"; [slack.teams] says {configured}" if configured else ""
        )
        team_rows += (
            f"<tr><td>{e(team)}</td><td>{e(note)}</td><td>"
            + _form(
                "set_team_channel",
                {"team": team},
                [
                    ("channel_name", channel.channel_name or "" if channel else "", "#team-web"),
                    ("channel_id", channel.channel_id or "" if channel else "", "C0123456"),
                ],
            )
            + "</td></tr>"
        )
    body = (
        "<article class='panel padded'><h2>People</h2>"
        "<p class='target-note'>Their Slack member id links the ping page to a DM.</p>"
        f"<table>{people_rows}</table>"
        "<h2>Teams</h2><p class='target-note'>The channel a team's pings go to;"
        " <code>[slack.teams]</code> in the config wins over what's here.</p>"
        f"<table>{team_rows}</table></article>"
    )
    return render.page("Where pings go", "slack contacts", body)


def look_up_requested(ctx: Context[SlackConfig], graph: Graph) -> list[str]:
    """Find, through Slack's API, the Slack ids of people my open PRs ask to review whom
    nothing names a Slack id for, by the email github knows for them. Each is looked up once:
    a miss is remembered too. Returns whom it found. Without a token, does nothing."""
    api_token = client.token(ctx.config, ctx.email)
    if api_token is None:
        return []
    slack = SlackRepository(ctx.db)
    known = slack.contacts()
    users, _ = _requested(graph)
    found: list[str] = []
    for login in sorted(users - set(known)):
        person = graph.view_one(PERSON, login, _PersonView)
        email = person.email if person else None
        if email is None:
            continue
        slack_id = client.lookup_by_email(api_token, email)
        slack.save_looked_up(login, slack_id, ctx.now)
        if slack_id:
            found.append(login)
    return found
