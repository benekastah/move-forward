"""`mfd slack`: see and set where review pings go; and what `mfd import` reads for slack,
the reviewer map and logged nudges from the skill's markdown."""

import argparse
import json
import re
import sys
from pathlib import Path

from ...api import ActionError, CliHandler, CliRequest, Context, SourceError, http
from . import client, contacts, discovery, markdown
from .config import SlackConfig
from .repository import SlackRepository


def import_markdown(ctx: Context[SlackConfig], notes_dir: Path) -> list[str]:
    """Read Slack ids from `reviewer-map.md`, and logged nudges from `work-notes.md`,
    replacing what an earlier import read; and set its teams' channels as if by hand, where
    none is set, naming the teams whose channel set already differs from the map's. Nothing
    when it has neither file. Can be run again any time."""
    names = (markdown.REVIEWER_MAP_FILE, markdown.WORK_NOTES_FILE)
    if not any((notes_dir / name).exists() for name in names):
        return []

    def read(name: str) -> str:
        path = notes_dir / name
        return path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""

    people, teams = markdown.read_reviewer_map(read(markdown.REVIEWER_MAP_FILE))
    pings = markdown.read_nudges(read(markdown.WORK_NOTES_FILE))
    slack = SlackRepository(ctx.db)
    set_teams = slack.replace_imported(people, teams, pings)
    kept = slack.team_channels()
    # Only a kept channel unlike the map's is worth a word: one equal to it changed nothing.
    differ = sorted(t.slug for t in teams if kept.get(t.slug) != t)
    said = ([f"set {len(set_teams)} teams' channels, as by hand"] if set_teams else []) + (
        [f"left {', '.join(differ)}, whose channel set already differs from the map's"]
        if differ
        else []
    )
    return [f"imported {len(people)} people and {len(pings)} logged nudges"] + (
        ["; ".join(said)] if said else []
    )


def list_destinations(
    ctx: Context[SlackConfig], as_json: bool, teams: list[str], discover: bool
) -> str:
    """Known destinations, or selected teams; optionally discover only missing mappings.
    Discovery is read-only, and ambiguous matches stay candidates rather than being saved."""
    slack = SlackRepository(ctx.db)
    sources = slack.sources()
    people = [
        {**c.model_dump(), "source": sources.get(f"user:{c.login}")}
        for c in slack.contacts().values()
    ]
    team_rows: list[dict[str, object]] = [
        {**t.model_dump(), "source": sources.get(f"team:{t.slug}")}
        for t in slack.team_channels().values()
    ]
    known = {str(t["slug"]): t for t in team_rows}
    for team, name in ctx.config.teams.items():
        current = known.get(team, {})
        known[team] = {
            "slug": team,
            "channel_name": name,
            "channel_id": current.get("channel_id")
            if current.get("channel_name") == name
            else None,
            "source": "config",
        }
    selected = (
        list(dict.fromkeys(teams))
        if teams
        else sorted(set(known) | (set(ctx.teams) if discover else set()))
    )
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", t) for t in selected):
        raise ValueError("use GitHub team slugs, e.g. bacon ai-agents")
    missing = [t for t in selected if t not in known]
    channels = client.channels(ctx.token()) if discover and missing else []
    team_rows = []
    for team in selected:
        if team in known:
            team_rows.append(known[team])
            continue
        row: dict[str, object] = {
            "slug": team,
            "channel_name": None,
            "channel_id": None,
            "source": None,
        }
        if discover:
            matches = discovery.candidates(team, channels)
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
        f"team {t['slug']}  {t['channel_name'] or ''} {t['channel_id'] or ''}  ({t['source']})"
        + (f" [{t['status']}]" if "status" in t else "")
        for t in team_rows
    ]
    for row in team_rows:
        if row.get("status") == "ambiguous":
            for match in discovery.candidates(str(row["slug"]), channels):
                lines.append(
                    f"  {match['channel_name']} ({match['channel_id']}); {match['matched_on']}"
                )
    return "\n".join(lines)


def run_command(ctx: Context[SlackConfig], args: argparse.Namespace) -> str:
    slack = SlackRepository(ctx.db)
    command = args.slack_command
    if command == "destinations":
        return list_destinations(ctx, args.json, args.teams, args.discover)
    if command == "contact" and args.contact_command == "set":
        return contacts.set_contact(slack, args.login, args.slack_id)
    if command == "contact":
        if not slack.unset_contact(args.login):
            raise ValueError(f"no Slack id is set for {args.login}")
        return f"forgot {args.login}'s Slack id"
    if args.team_command == "set":
        return contacts.set_team_channel(slack, args.team, args.channel, args.id or "")
    if not slack.unset_team_channel(args.team):
        raise ValueError(f"no channel is set for {args.team}")
    return f"forgot {args.team}'s channel"


def add_commands(parser: argparse.ArgumentParser) -> CliHandler:
    commands = parser.add_subparsers(dest="slack_command", required=True)
    destinations = commands.add_parser("destinations", help="where people's and teams' pings go")
    destinations.add_argument("teams", nargs="*", help="GitHub team slugs to look up")
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
    team_set.add_argument("team", help="the team as PRs name it")
    team_set.add_argument("channel", help="its channel's name, e.g. #team-web")
    team_set.add_argument("--id", help="the channel's id, e.g. C0123456")
    team_commands.add_parser("unset").add_argument("team")

    def run(req: CliRequest[SlackConfig]) -> int:
        ctx, args = req.ctx, req.args
        try:
            print(run_command(ctx, args))
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
