"""`mfd slack`: import the reviewer map and logged nudges from the skill's markdown."""

import argparse
import sys
from pathlib import Path

from ...graph import Graph
from ...plugin import CliHandler, Context
from . import markdown
from .config import SlackConfig
from .repository import SlackRepository


def import_markdown(ctx: Context[SlackConfig], notes_dir: Path) -> str:
    """Read Slack ids and team channels from `reviewer-map.md`, and logged nudges from
    `work-notes.md`, replacing what an earlier import read. Can be run again any time."""

    def read(name: str) -> str:
        path = notes_dir / name
        return path.read_text() if path.exists() else ""

    contacts, teams = markdown.read_reviewer_map(read(markdown.REVIEWER_MAP_FILE))
    pings = markdown.read_nudges(read(markdown.WORK_NOTES_FILE))
    SlackRepository(ctx.db).replace_imported(contacts, teams, pings)
    return f"imported {len(contacts)} people, {len(teams)} teams and {len(pings)} logged nudges"


def add_commands(parser: argparse.ArgumentParser) -> CliHandler:
    commands = parser.add_subparsers(dest="slack_command", required=True)
    import_parser = commands.add_parser(
        "import", help="read reviewer-map.md (and nudges in work-notes.md) from a directory"
    )
    import_parser.add_argument("dir", type=Path)

    def run(ctx: Context[SlackConfig], graph: Graph, args: argparse.Namespace) -> int:
        notes_dir = Path(args.dir).expanduser()
        if not notes_dir.is_dir():
            print(f"mfd slack import: {notes_dir} isn't a directory", file=sys.stderr)
            return 1
        print(import_markdown(ctx, notes_dir))
        return 0

    return run
