"""The `mfd` command."""

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from . import config as config_module
from . import init, reload
from .db import Database
from .refresh import build_dashboard, refresh
from .server import serve

EXAMPLE_CONFIG = """\
# move-forward configuration. Paths may start with ~. A plugin is enabled by its
# section: leave one out to turn the plugin off.

email = "me@example.com"              # Jira logs in with it; its domain finds reviewers' emails
teams = ["my-team"]                   # the teams I'm on or review for, by name
# notes_dir = "~/.config/move-forward/notes"  # work-notes.md, on-ice.md, reviewer-map.md
# db_path = "~/.local/share/move-forward/dashboard.db"
# port = 8765
# refresh_seconds = 300                  # background refresh when nobody loads the page
# refresh_on_load_seconds = 60           # a page load refreshes data older than this
# prune_days = 90                         # clear work finished this long ago from the notes

[github]
org = "example-org"
# history_days = 14
# review_checks = []                     # checks that report review status, not CI
# required_owner_marker = ""             # CODEOWNERS comment marking required owners
# ticket_link_prefix = ""                # PR body line that links the ticket, e.g. "Link"
# placeholder_labels = []                # template labels to replace, e.g. ["needs-team"]
# email_domains = []                     # reviewers' email domains, if not just your email's
# stale_review_hours = 24
# reviewer_history_days = 180

# [github.teams]                        # a team's slug, where it isn't the team's name
# my-team = "my-team-reviewers"

[git]
repos = ["~/src/app", "~/src/infra"]  # main checkouts; other worktrees are found from them
# my_branch_prefixes = ["me/"]            # else every local branch counts as mine
# ignored_worktree_globs = ["*/.claude/worktrees/*"]
# commit_history_days = 180

[slack]
workspace_url = "https://example.slack.com"
# ping_first = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
# ping_follow_up = "Gentle bump on this review request"

[slack.teams]                         # where review pings to a team go
my-team = "#team-my-team"

# [jira]
# site = "https://example.atlassian.net"
# token_env = "JIRA_API_TOKEN"            # else the macOS keychain item for my email
# keychain_service = "move-forward-jira"
# status_in_progress = "In Progress"      # workflow names the transitions panel uses
# status_in_review = "In Review"
# status_closed = "Closed"
# escalation_label_prefix = ""            # labels that rank incidents, e.g. "escalated-to-"
# escalation_levels = []                  # what follows it, lowest first, e.g. ["manager", "director", "vp"]
# incident_types = ["incident"]           # issue types (substrings, lower-case)
# bug_types = ["bug"]
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mfd", description="Local dashboard of work in flight.")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=config_module.DEFAULT_CONFIG_PATH,
        help=f"config file (default: {config_module.DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    serve_parser = commands.add_parser("serve", help="refresh in the background and serve the page")
    serve_parser.add_argument("--port", type=int, help="override the configured port")
    serve_parser.add_argument("--open", action="store_true", help="open the page in a browser")
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="development: restart when the dashboard's source files change",
    )

    commands.add_parser("refresh", help="refresh every source once and exit")
    commands.add_parser("dump", help="print the dashboard as JSON from what is stored")
    init_parser = commands.add_parser("init", help="ask for each setting and write the config")
    init_parser.add_argument(
        "--example", action="store_true", help="write a commented example instead of asking"
    )
    init_parser.add_argument("--force", action="store_true", help="overwrite an existing config")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config_path: Path = args.config

    if args.command == "init":
        if config_path.exists() and not args.force:
            print(f"{config_path} already exists; pass --force to replace it", file=sys.stderr)
            return 1
        if args.example:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(EXAMPLE_CONFIG)
            print(f"wrote {config_path}; edit it, then run `mfd serve --open`")
            return 0
        try:
            print(init.init(config_path))
        except (EOFError, KeyboardInterrupt):
            print("\ninit cancelled; nothing written", file=sys.stderr)
            return 1
        return 0

    if not config_path.exists():
        print(f"no config at {config_path}; run `mfd init` first", file=sys.stderr)
        return 1
    config = config_module.load(config_path)
    db = Database(config.db_path)

    if args.command == "refresh":
        refresh(config, db)
        dashboard = build_dashboard(config, db)
        for source, status in dashboard.sources.items():
            print(f"{source}: {'error: ' + status.error if status.error else 'ok'}")
        return 0
    if args.command == "dump":
        print(build_dashboard(config, db).model_dump_json(indent=2))
        return 0
    if args.command == "serve":
        port = args.port or config.port
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        if args.reload:
            reload.start()
        serve(config, db, port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
