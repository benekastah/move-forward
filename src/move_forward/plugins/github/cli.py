"""`mfd github`: import reviewers' emails from the skill's reviewer map."""

import argparse
import sys
from pathlib import Path

from ...graph import Graph
from ...plugin import CliHandler, Context
from .config import GitHubConfig
from .people import REVIEWER_MAP_FILE, PeopleRepository, read_reviewer_map


def add_commands(parser: argparse.ArgumentParser) -> CliHandler:
    commands = parser.add_subparsers(dest="github_command", required=True)
    import_parser = commands.add_parser(
        "import", help="read reviewers' emails from reviewer-map.md in a directory"
    )
    import_parser.add_argument("dir", type=Path)

    def run(ctx: Context[GitHubConfig], graph: Graph, args: argparse.Namespace) -> int:
        path = Path(args.dir).expanduser() / REVIEWER_MAP_FILE
        if not path.exists():
            print(f"mfd github import: no {path}", file=sys.stderr)
            return 1
        emails = read_reviewer_map(path.read_text())
        PeopleRepository(ctx.db).replace_imported(emails)
        print(f"imported {len(emails)} reviewers' emails")
        return 0

    return run
