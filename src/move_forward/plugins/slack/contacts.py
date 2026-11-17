"""Where review pings go, viewed and edited: the `/slack/contacts` page, its buttons, and
what `mfd slack` shares with them."""

import re
from string.templatelib import Template

from move_forward.api import (
    BY_HAND,
    IMPORTED,
    LOOKED_UP,
    Action,
    ActionError,
    Context,
    Graph,
    InputRequest,
    Model,
    SourceError,
    html,
    http,
)
from move_forward.api.kinds import PERSON, TEAM
from move_forward.plugins.slack import client
from move_forward.plugins.slack.config import TEAM_ID_RE, SlackConfig, team_id
from move_forward.plugins.slack.pings import my_open_prs
from move_forward.plugins.slack.repository import SlackRepository

CONTACTS = "contacts"
"""Where pings go, the page: `/slack/contacts`."""
CONTACTS_PATH = f"/slack/{CONTACTS}"
SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{6,}$")
CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{6,}$")
CHANNEL_NAME_RE = re.compile(r"^#[\w-]+$")
LOOKUPS_FAILED = "lookups"
"""The problem (`Context.problem`) of why looking up Slack ids by email last failed."""
BAD_TOKEN = {
    "invalid_auth",
    "not_authed",
    "token_expired",
    "token_revoked",
    "account_inactive",
    "not_allowed_token_type",
}
"""Slack's refusals of the token itself, rather than of one lookup."""


class _PersonView(Model):
    """What slack reads of a `person`, found by their GitHub login (`handle`): their commit
    email."""

    email: str | None = None


class _TeamView(Model):
    """What slack reads of a `team`: its `id`, which PRs name it by, and whether it's mine."""

    id: str
    mine: bool = False


def my_teams(graph: Graph) -> set[str]:
    """The ids of the teams I'm on or review for, as the plugins publishing teams say,
    casefolded (see `config.team_id`)."""
    return {team.id.casefold() for team in graph.view(TEAM, _TeamView) if team.mine}


def teams_host(graph: Graph) -> str | None:
    """The host the graph's teams are on, if they're all on one: for an example of an id."""
    hosts = {team.id.partition("/")[0] for team in graph.view(TEAM, _TeamView)}
    return hosts.pop() if len(hosts) == 1 else None


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


def set_team_channel(
    slack: SlackRepository, team: str, name: str, channel_id: str, host: str | None = None
) -> str:
    """Set where a team's pings go, by hand. A team that isn't named by its id is refused,
    with an example on `host` (see `teams_host`)."""
    try:
        team = team_id(team, host)
    except ValueError as e:
        raise ActionError(str(e)) from None
    channel_name = _optional(name, CHANNEL_NAME_RE, "channel name, e.g. #team-web")
    channel = _optional(channel_id, CHANNEL_ID_RE, "channel id, e.g. C0123456")
    if channel_name is None and channel is None:
        raise ActionError("a channel name or id is needed")
    slack.set_team_channel(team, channel_name, channel)
    return f"{team}'s pings go to {channel_name or channel}"


def clear_team_channel_action(req: InputRequest[SlackConfig]) -> str:
    """Forget a team's channel, as `mfd slack team unset` does."""
    team = req.field("team")
    if not SlackRepository(req.ctx.db).unset_team_channel(team):
        raise ActionError(f"{team} has no channel set")
    return f"forgot {team}'s channel"


def clear_contact_action(req: InputRequest[SlackConfig]) -> str:
    """Forget where a person's pings go, as `mfd slack contact unset` does, so they're imported
    or looked up again."""
    login = req.field("login")
    if not SlackRepository(req.ctx.db).unset_contact(login):
        raise ActionError(f"nothing is kept for {login}")
    return f"forgot what was kept for {login}; they're looked up again"


def set_contact_action(req: InputRequest[SlackConfig]) -> str:
    ctx, fields = req.ctx, req.form.fields
    return set_contact(SlackRepository(ctx.db), req.field("login"), fields.get("slack_id", ""))


def set_team_channel_action(req: InputRequest[SlackConfig]) -> str:
    ctx, fields = req.ctx, req.form.fields
    return set_team_channel(
        SlackRepository(ctx.db),
        req.field("team"),
        fields.get("channel_name", ""),
        fields.get("channel_id", ""),
        teams_host(req.graph),
    )


def _requested(graph: Graph) -> tuple[set[str], set[str]]:
    """The people and teams my open PRs ask to review, by login and team id (casefolded)."""
    users: set[str] = set()
    teams: set[str] = set()
    for pr in my_open_prs(graph):
        users.update(pr.requested_users)
        teams.update(team.casefold() for team in pr.requested_teams)
    return users, teams


def _form(action: str, hidden: dict[str, str], inputs: list[tuple[str, str, str]]) -> html.Html:
    fields = [t"<input type='hidden' name='{k}' value='{v}'>" for k, v in hidden.items()]
    fields += [
        t"<input type='text' name='{name}' value='{value}' placeholder='{hint}'>"
        for name, value, hint in inputs
    ]
    return html.html(
        t"<form method='post' action='/actions/slack/{action}?back={CONTACTS_PATH}'"
        t" class='action'>{fields}<button type='submit' class='secondary small'>Save</button>"
        t"</form>"
    )


def contacts_page(ctx: Context[SlackConfig], graph: Graph) -> html.Html:
    """Everyone and every team pings can go to: the ones my open PRs ask for, my teams, and the
    ones imported or set, with where their pings go and an edit form each; and why the last
    lookup by email failed, if it did."""
    slack = SlackRepository(ctx.db)
    contacts, channels, sources = slack.contacts(), slack.team_channels(), slack.sources()
    users, teams = _requested(graph)

    def how(target: str) -> str:
        source = sources.get(target)
        return {BY_HAND: "set by hand", IMPORTED: "imported", LOOKED_UP: "looked up"}.get(
            source or "", "not known"
        )

    def clear(name: str, fields: dict[str, str], confirm: str) -> html.Html:
        action = Action(name=name, label="Clear", fields=fields, confirm=confirm)
        return html.buttons([action], back=CONTACTS_PATH)

    people_rows: list[Template] = []
    for login in sorted(users | set(contacts)):
        slack_id = contacts[login].slack_id or "" if login in contacts else ""
        form = _form("set_contact", {"login": login}, [("slack_id", slack_id, "U0123456")])
        forget = f"Forget what's kept for {login}? They're imported or looked up again."
        cleared = (
            clear("slack/clear_contact", {"login": login}, forget) if login in contacts else ""
        )
        people_rows.append(
            t"<tr><td>{login}</td><td>{how(f'user:{login}')}</td><td>{form}{cleared}</td></tr>"
        )
    team_rows: list[Template] = []
    for team in sorted(teams | my_teams(graph) | set(channels) | set(ctx.config.teams)):
        channel = channels.get(team)
        configured = ctx.config.teams.get(team)
        # A channel kept by slug alone matches no team: it's only there to clear.
        by_slug = not TEAM_ID_RE.match(team)
        note = f"{how(f'team:{team}')}" + (
            f"; [slack.teams] says {configured}" if configured else ""
        )
        if by_slug:
            note = "kept by slug: set it again by the team's id, then clear this"
        form = (
            ""
            if by_slug
            else _form(
                "set_team_channel",
                {"team": team},
                [
                    ("channel_name", channel.channel_name or "" if channel else "", "#team-web"),
                    ("channel_id", channel.channel_id or "" if channel else "", "C0123456"),
                ],
            )
        )
        forget = f"Forget {team}'s channel?"
        cleared = clear("slack/clear_team_channel", {"team": team}, forget) if channel else ""
        team_rows.append(t"<tr><td>{team}</td><td>{note}</td><td>{form}{cleared}</td></tr>")
    body = html.html(
        t"<article class='panel padded'>"
        t"<h2>People</h2>"
        t"<p class='target-note'>Their Slack member id links the ping page to a DM.</p>"
        t"<table>{people_rows}</table>"
        t"<h2>Teams</h2><p class='target-note'>The channel a team's pings go to;"
        t" <code>[slack.teams]</code> in the config wins over what's here.</p>"
        t"<table>{team_rows}</table></article>"
    )
    return html.page("Where pings go", "where pings go", body)


def _hint(failure: Exception, token_env: str) -> str:
    """What to do about a lookup Slack refused for its token, after the refusal: give it the
    scope it lacks, or a token that works (where slack reads one: `token_env` first)."""
    if not isinstance(failure, client.SlackError):
        return ""
    if failure.error == "missing_scope":
        return ": the token needs users:read.email"
    if failure.error in BAD_TOKEN:
        return f": give slack a token that works: set ${token_env}, or `mfd token set slack`"
    return ""


def look_up_requested(ctx: Context[SlackConfig], graph: Graph) -> list[str]:
    """Find, through Slack's API, the Slack ids of people my open PRs ask to review whom
    nothing names a Slack id for, by the email github knows for them. Each is looked up once:
    a miss is remembered too. Returns whom it found. Without a token, does nothing. A lookup
    that fails (a token without `users:read.email`, a rate limit, no network) stops this
    refresh's, and is the problem `LOOKUPS_FAILED` until a refresh's lookups don't fail."""
    try:
        api_token = ctx.token()
    except SourceError:  # none given: no lookups
        ctx.problem(LOOKUPS_FAILED, None)
        return []
    slack = SlackRepository(ctx.db)
    known = slack.contacts()
    users, _ = _requested(graph)
    found: list[str] = []
    for login in sorted(users - set(known)):
        people = graph.view_by_handle(PERSON, login, _PersonView)
        email = next((p.email for p in people if p.email), None)
        if email is None:
            continue
        try:
            slack_id = client.lookup_by_email(api_token, email)
        # Slack's refusal, no network or a cut answer, or one that isn't JSON.
        except (client.SlackError, http.RequestError, ValueError) as e:
            ctx.problem(
                LOOKUPS_FAILED,
                "looking up reviewers' Slack ids by email failed:"
                f" {e}{_hint(e, ctx.config.token_env)}",
            )
            return found
        slack.save_looked_up(login, slack_id, ctx.now)
        if slack_id:
            found.append(login)
    ctx.problem(LOOKUPS_FAILED, None)
    return found
