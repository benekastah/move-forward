"""`mfd slack`: see and set where review pings go, and import the reviewer map and logged
nudges from the skill's markdown."""

import argparse
import json
import sys
from pathlib import Path

from ...graph import Graph
from ...plugin import ActionError, CliHandler, Context
from . import contacts, markdown
from .config import SlackConfig
from .repository import SlackRepository


def import_markdown(ctx: Context[SlackConfig], notes_dir: Path) -> str:
    """Read Slack ids and team channels from `reviewer-map.md`, and logged nudges from
    `work-notes.md`, replacing what an earlier import read. Can be run again any time."""

    def read(name: str) -> str:
        path = notes_dir / name
        return path.read_text() if path.exists() else ""

    if not notes_dir.is_dir():
        raise ValueError(f"{notes_dir} isn't a directory")
    people, teams = markdown.read_reviewer_map(read(markdown.REVIEWER_MAP_FILE))
    pings = markdown.read_nudges(read(markdown.WORK_NOTES_FILE))
    SlackRepository(ctx.db).replace_imported(people, teams, pings)
    return f"imported {len(people)} people, {len(teams)} teams and {len(pings)} logged nudges"


def list_contacts(ctx: Context[SlackConfig], as_json: bool) -> str:
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
    if command == "import":
        return import_markdown(ctx, Path(args.dir).expanduser())
    if command == "contacts":
        return list_contacts(ctx, args.json)
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
    import_parser = commands.add_parser(
        "import", help="read reviewer-map.md (and nudges in work-notes.md) from a directory"
    )
    import_parser.add_argument("dir", type=Path)
    commands.add_parser("contacts", help="where people's and teams' pings go").add_argument(
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

    def run(ctx: Context[SlackConfig], graph: Graph, args: argparse.Namespace) -> int:
        try:
            print(run_command(ctx, args))
        except (ValueError, ActionError) as e:
            print(f"mfd slack {args.slack_command}: {e}", file=sys.stderr)
            return 1
        return 0

    return run
