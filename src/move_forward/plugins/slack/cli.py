"""`mfd slack`: see and set where review pings go; and what `mfd import` reads for slack,
the reviewer map and logged nudges from the skill's markdown."""

import argparse
import json
import sys
from pathlib import Path

from ...api import ActionError, CliHandler, CliRequest, Context
from . import contacts, markdown
from .config import SlackConfig
from .repository import SlackRepository


def import_markdown(ctx: Context[SlackConfig], notes_dir: Path) -> list[str]:
    """Read Slack ids from `reviewer-map.md`, and logged nudges from `work-notes.md`,
    replacing what an earlier import read; and set its teams' channels as if by hand, where
    none is set. Nothing when it has neither file. Can be run again any time."""
    names = (markdown.REVIEWER_MAP_FILE, markdown.WORK_NOTES_FILE)
    if not any((notes_dir / name).exists() for name in names):
        return []

    def read(name: str) -> str:
        path = notes_dir / name
        return path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""

    people, teams = markdown.read_reviewer_map(read(markdown.REVIEWER_MAP_FILE))
    pings = markdown.read_nudges(read(markdown.WORK_NOTES_FILE))
    set_teams = SlackRepository(ctx.db).replace_imported(people, teams, pings)
    left = sorted({t.slug for t in teams} - set(set_teams))
    return [
        f"imported {len(people)} people and {len(pings)} logged nudges",
        f"set {len(set_teams)} teams' channels, as by hand"
        + (f"; left {', '.join(left)}, which had one set already" if left else ""),
    ]


def list_destinations(ctx: Context[SlackConfig], as_json: bool) -> str:
    """People's Slack ids and teams' channels, each with where it came from."""
    slack = SlackRepository(ctx.db)
    sources = slack.sources()
    people = [
        {**c.model_dump(), "source": sources.get(f"user:{c.login}")}
        for c in slack.contacts().values()
    ]
    teams = [
        {**t.model_dump(), "source": sources.get(f"team:{t.slug}")}
        for t in slack.team_channels().values()
    ]
    if as_json:
        return json.dumps({"people": people, "teams": teams}, indent=2)
    lines = [f"{p['login']}  {p['slack_id'] or '-'}  ({p['source']})" for p in people]
    lines += [
        f"team {t['slug']}  {t['channel_name'] or ''} {t['channel_id'] or ''}  ({t['source']})"
        for t in teams
    ]
    return "\n".join(lines)


def run_command(ctx: Context[SlackConfig], args: argparse.Namespace) -> str:
    slack = SlackRepository(ctx.db)
    command = args.slack_command
    if command == "destinations":
        return list_destinations(ctx, args.json)
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
    commands.add_parser("destinations", help="where people's and teams' pings go").add_argument(
        "--json", action="store_true", help="print JSON, for scripts"
    )
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
        except (ValueError, ActionError) as e:
            print(f"mfd slack {args.slack_command}: {e}", file=sys.stderr)
            return 1
        return 0

    return run
