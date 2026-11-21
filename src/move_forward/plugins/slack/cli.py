"""`mfd slack`: see and set where review pings go."""

import argparse
import json
import sys

from move_forward.api import (
    ActionError,
    CliHandler,
    CliRequest,
    Context,
    Graph,
    SourceError,
    http,
)
from move_forward.plugins.slack import client, contacts, discovery
from move_forward.plugins.slack.config import SlackConfig, team_id
from move_forward.plugins.slack.repository import SlackRepository


def list_destinations(
    ctx: Context[SlackConfig], graph: Graph, as_json: bool, teams: list[str], discover: bool
) -> str:
    """Known destinations, or selected teams; optionally discover only missing mappings, of my
    teams (`contacts.my_teams`) too. Discovery is read-only, and ambiguous matches stay
    candidates rather than being saved."""
    slack = SlackRepository(ctx.db)
    sources = slack.sources()
    people = [
        {**c.model_dump(), "source": sources.get(f"user:{c.login}")}
        for c in slack.contacts().values()
    ]
    team_rows: list[dict[str, object]] = [
        {**t.model_dump(), "source": sources.get(f"team:{t.team}")}
        for t in slack.team_channels().values()
    ]
    known = {str(t["team"]): t for t in team_rows}
    for team, name in ctx.config.teams.items():
        current = known.get(team, {})
        known[team] = {
            "team": team,
            "channel_name": name,
            "channel_id": current.get("channel_id")
            if current.get("channel_name") == name
            else None,
            "source": "config",
        }
    teams = [team_id(t, contacts.teams_host(graph)) for t in teams]
    selected = (
        list(dict.fromkeys(teams))
        if teams
        else sorted(set(known) | (contacts.my_teams(graph) if discover else set()))
    )
    missing = [t for t in selected if t not in known]
    channels = client.channels(ctx.token()) if discover and missing else []
    team_rows = []
    for team in selected:
        if team in known:
            team_rows.append(known[team])
            continue
        row: dict[str, object] = {
            "team": team,
            "channel_name": None,
            "channel_id": None,
            "source": None,
        }
        if discover:
            matches = discovery.candidates(_slug(team), channels)
            row["candidates"] = matches
            row["status"] = (
                "discovered" if len(matches) == 1 else "ambiguous" if matches else "missing"
            )
            if len(matches) == 1:
                row.update({k: matches[0][k] for k in ("channel_name", "channel_id")})
                row["source"] = "discovered"
        team_rows.append(row)
    if teams:
        people = []
    if as_json:
        return json.dumps({"people": people, "teams": team_rows}, indent=2)
    lines = [f"{p['login']}  {p['slack_id'] or '-'}  ({p['source']})" for p in people]
    lines += [
        f"team {t['team']}  {t['channel_name'] or ''} {t['channel_id'] or ''}  ({t['source']})"
        + (f" [{t['status']}]" if "status" in t else "")
        for t in team_rows
    ]
    for row in team_rows:
        if row.get("status") == "ambiguous":
            for match in discovery.candidates(_slug(str(row["team"])), channels):
                lines.append(
                    f"  {match['channel_name']} ({match['channel_id']}); {match['matched_on']}"
                )
    return "\n".join(lines)


def _slug(team: str) -> str:
    """A team's slug: the last part of its id, which channels are named for."""
    return team.rpartition("/")[2]


def run_command(ctx: Context[SlackConfig], graph: Graph, args: argparse.Namespace) -> str:
    slack = SlackRepository(ctx.db)
    command = args.slack_command
    if command == "destinations":
        return list_destinations(ctx, graph, args.json, args.teams, args.discover)
    if command == "contact" and args.contact_command == "set":
        return contacts.set_contact(slack, args.login, args.slack_id)
    if command == "contact":
        if not slack.unset_contact(args.login):
            raise ValueError(f"no Slack id is set for {args.login}")
        return f"forgot {args.login}'s Slack id"
    if args.team_command == "set":
        return contacts.set_team_channel(
            slack, args.team, args.channel, args.id or "", contacts.teams_host(graph)
        )
    if not slack.unset_team_channel(args.team):
        raise ValueError(f"no channel is set for {args.team}")
    return f"forgot {args.team}'s channel"


def add_commands(parser: argparse.ArgumentParser) -> CliHandler:
    commands = parser.add_subparsers(dest="slack_command", required=True)
    destinations = commands.add_parser("destinations", help="where people's and teams' pings go")
    destinations.add_argument(
        "teams", nargs="*", help="the ids of the teams to look up, e.g. github.com/acme/web"
    )
    destinations.add_argument(
        "--discover",
        action="store_true",
        help="find missing teams' channels by name, topic and purpose (read-only)",
    )
    destinations.add_argument("--json", action="store_true", help="print JSON, for scripts")
    contact = commands.add_parser("contact", help="set or forget a person's Slack id")
    contact_commands = contact.add_subparsers(dest="contact_command", required=True)
    contact_set = contact_commands.add_parser("set")
    contact_set.add_argument("login", help="their GitHub login")
    contact_set.add_argument("slack_id", help="their Slack member id, e.g. U0123456")
    contact_commands.add_parser("unset").add_argument("login")
    team = commands.add_parser("team", help="set or forget a team's channel")
    team_commands = team.add_subparsers(dest="team_command", required=True)
    team_set = team_commands.add_parser("set")
    team_set.add_argument("team", help="the team's id, e.g. github.com/acme/web")
    team_set.add_argument("channel", help="its channel's name, e.g. #team-web")
    team_set.add_argument("--id", help="the channel's id, e.g. C0123456")
    team_commands.add_parser("unset").add_argument("team")

    def run(req: CliRequest[SlackConfig]) -> int:
        ctx, args = req.ctx, req.args
        try:
            print(run_command(ctx, req.graph, args))
        except (ValueError, ActionError, SourceError, client.SlackError, http.RequestError) as e:
            hint = (
                "; channel discovery needs channels:read and groups:read on the Slack token"
                if isinstance(e, client.SlackError) and e.error == "missing_scope"
                else ""
            )
            print(f"mfd slack {args.slack_command}: {e}{hint}", file=sys.stderr)
            return 1
        return 0

    return run
