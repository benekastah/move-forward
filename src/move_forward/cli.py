"""The `mfd` command."""

import argparse
import getpass
import logging
import os
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from . import auth, init, keychain, localtime, registry, supervisor, tls
from . import config as config_module
from .db import Database
from .plugin import CliHandler
from .refresh import build_dashboard, refresh, stored_graph
from .registry import enabled, enabled_names, migrate
from .server import scheme, serve
from .shell import CommandError

EXAMPLE_CONFIG = """\
# move-forward configuration. Paths may start with ~. A plugin is enabled by its
# section: leave one out to turn the plugin off.

email = "me@example.com"              # Jira logs in with it; its domain finds reviewers' emails
teams = ["my-team"]                   # the teams I'm on or review for, by name
# db_path = "~/.local/share/move-forward/dashboard.db"
# db_backups = 10                        # copies kept in backups/, taken before migrations
# port = 8765
# https = false                          # serve over HTTPS with `mfd cert`'s certificate
# refresh_on_load_seconds = 60           # a page load refreshes data older than this
# prune_days = 90                         # clear work finished this long ago from the notes

[github]
org = "example-org"
# refresh_seconds = 300
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
# refresh_seconds = 60
# my_branch_prefixes = ["me/"]            # besides branches with my commits or PRs
# ignored_worktree_globs = ["*/.claude/worktrees/*"]
# commit_history_days = 180

[git_stack]                           # stacks from git-stack, in [git]'s repos; no settings

[slack]
workspace_url = "https://example.slack.com"
# client_id = ""                          # the workspace's Slack app, to connect on slack's page
# ping_first = "Hi, would you mind reviewing {url} when you get a chance? Thanks in advance!"
# ping_follow_up = "Gentle bump on this review request"
# token_env = "SLACK_API_TOKEN"           # a token with users:read.email, to find Slack ids by email
# keychain_service = "move-forward-slack"
# team_channel = "#team-{team}"           # a team's channel when none is set

[slack.teams]                         # where review pings to a team go
my-team = "#team-my-team"

# [jira]
# site = "https://example.atlassian.net"
# refresh_seconds = 900
# token_env = "JIRA_API_TOKEN"            # else the system's secret store, for my email
# keychain_service = "move-forward-jira"
# status_in_progress = "In Progress"      # workflow names the transitions panel uses
# status_in_review = "In Review"
# status_closed = "Closed"
# escalation_label_prefix = ""            # labels that rank incidents, e.g. "escalated-to-"
# escalation_levels = []                  # what follows it, lowest first, e.g. ["manager", "director", "vp"]
# incident_types = ["incident"]           # issue types (substrings, lower-case)
# bug_types = ["bug"]

# [act_now]                             # how Act now ranks what the plugins nominate
# size = 7                               # items shown
# per_plugin = 3                         # so one busy tool can't fill the list
# weights = { closes = 3, minutes = 2, hour = 1, waiting = 2, importance = 3, due = 1 }  # what each signal adds

"""


def _parser() -> tuple[argparse.ArgumentParser, dict[str, CliHandler]]:
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
    serve_parser.add_argument(
        "--open", action="store_true", help="open the page in a browser, signed in"
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="development: restart when the dashboard's source files change, not just its config",
    )

    cert_parser = commands.add_parser(
        "cert", help="make the certificate `mfd serve` uses for HTTPS, and trust it"
    )
    cert_parser.add_argument(
        "--trust", action="store_true", help="add it to this system's and browsers' stores"
    )
    cert_parser.add_argument(
        "--force", action="store_true", help="make a new one even if one is there"
    )
    open_parser = commands.add_parser("open", help="open the running dashboard, signed in")
    open_parser.add_argument("--port", type=int, help="override the configured port")
    commands.add_parser("refresh", help="refresh every source once and exit")
    commands.add_parser(
        "check", help="load the config and every plugin, and exit; fails with what's wrong"
    )
    commands.add_parser("dump", help="print the dashboard as JSON from what is stored")
    init_parser = commands.add_parser("init", help="ask for each setting and write the config")
    init_parser.add_argument(
        "--example", action="store_true", help="write a commented example instead of asking"
    )
    init_parser.add_argument("--force", action="store_true", help="overwrite an existing config")
    token_parser = commands.add_parser(
        "token", help="store a plugin's API token in the system's secret store, or check it"
    )
    token_commands = token_parser.add_subparsers(dest="token_command", required=True)
    set_parser = token_commands.add_parser("set", help="ask for a token and store it")
    set_parser.add_argument("plugin", choices=_token_plugins())
    token_commands.add_parser("check", help="say where each plugin's token is found")
    plugin_commands = {
        plugin.name: plugin.cli(commands.add_parser(plugin.name, help=plugin.cli_help))
        for plugin in registry.PLUGINS
        if plugin.cli_help
    }
    return parser, plugin_commands


def _token_plugins() -> list[str]:
    """The plugins whose section says where their token is (see `keychain.TokenSettings`)."""
    fields = set(keychain.TokenSettings.__annotations__)
    return [p.name for p in registry.PLUGINS if fields <= p.config_model.model_fields.keys()]


def _token(config: config_module.Config, config_path: Path, args: argparse.Namespace) -> int:
    """`mfd token set <plugin>`: ask for the plugin's token without echoing it and store it
    in the system's secret store, for the top-level `email`. `mfd token check`: say where
    each enabled plugin's token is found, without showing it."""
    sections = {
        name: section
        for name in enabled_names(config)
        if isinstance(section := getattr(config, name), keychain.TokenSettings)
    }
    if args.token_command == "check":
        for name, section in sections.items():
            if os.environ.get(section.token_env):
                print(f"{name}: from ${section.token_env}")
            elif keychain.read(section.keychain_service, config.email):
                print(f"{name}: from the secret store ({section.keychain_service})")
            else:
                print(f"{name}: missing; run `mfd token set {name}`")
        return 0
    section = sections.get(args.plugin)
    if section is None:
        print(f"[{args.plugin}] isn't in {config_path}", file=sys.stderr)
        return 1
    try:
        secret = getpass.getpass(f"{args.plugin} API token: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nnothing stored", file=sys.stderr)
        return 1
    try:
        keychain.write(section.keychain_service, config.email, secret)
    except FileNotFoundError:
        print(
            f"no secret store: install `secret-tool` (libsecret), or set ${section.token_env}",
            file=sys.stderr,
        )
        return 1
    except (ValueError, CommandError) as e:
        print(f"not stored: {e}", file=sys.stderr)
        return 1
    print(f"stored under {section.keychain_service} for {config.email}")
    if os.environ.get(section.token_env):
        print(f"note: ${section.token_env} is set, and is used before the secret store")
    return 0


def _cert(paths: tls.Paths, force: bool, trust: bool) -> int:
    """`mfd cert`: make the certificates unless they're there, and trust them if asked."""
    if paths.ready() and not force:
        print(
            f"certificates in {paths.dir} cover until {localtime.day(tls.expires(paths))};"
            " --force replaces them"
        )
    else:
        tls.make(paths)
        print(
            f"made certificates in {paths.dir} covering until {localtime.day(tls.expires(paths))};"
            " `mfd serve` moves from one to the next by itself"
        )
    if trust:
        for line in tls.trust(paths):
            print(line)
    else:
        print(f"trust {paths.ca} with `mfd cert --trust`, so browsers don't warn")
    return 0


def _open(url: str) -> None:
    """Open the dashboard in a browser; print the URL when there's no browser to open."""
    if not webbrowser.open(url):
        print(f"open {url} in a browser to sign in")


def main(argv: list[str] | None = None) -> int:
    parser, plugin_commands = _parser()
    args = parser.parse_args(argv)
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
    if args.command == "check":
        try:
            checked = config_module.load(config_path)
        except (OSError, ValueError) as e:  # tomllib and pydantic errors are ValueErrors
            print(f"{config_path}: {e}", file=sys.stderr)
            return 1
        print(f"{config_path}: ok; plugins: {', '.join(enabled_names(checked))}")
        if checked.https and (problem := tls.problem(tls.Paths.beside(checked.db_path))):
            print(f"warning: {problem}")
        return 0
    config = config_module.load(config_path)
    if args.command == "token":
        return _token(config, config_path, args)
    if args.command == "serve" and not supervisor.supervised():
        # The server runs in a child process, which this one replaces on a change (see
        # `supervisor`); the child opens the database and serves.
        full_argv = [sys.argv[0], *(sys.argv[1:] if argv is None else argv)]
        return supervisor.supervise(full_argv, config_path, code=args.reload)
    token = auth.load_or_create(auth.token_path(config.db_path))
    if args.command == "open":
        _open(auth.login_url(args.port or config.port, token, scheme(config)))
        return 0
    if args.command == "cert":
        return _cert(tls.Paths.beside(config.db_path), args.force, args.trust)
    db = Database(config.db_path, config.db_backups)
    migrate(config, db)

    if args.command == "refresh":
        refresh(config, db)
        dashboard = build_dashboard(config, db)
        for source, status in dashboard.sources.items():
            print(f"{source}: {'error: ' + status.error if status.error else 'ok'}")
        return 0
    if args.command == "dump":
        print(build_dashboard(config, db).model_dump_json(indent=2))
        return 0
    if args.command in plugin_commands:
        plugins = enabled(config, db, datetime.now(UTC))
        e = next((e for e in plugins if e.name == args.command), None)
        if e is None:
            print(f"[{args.command}] isn't in {config_path}", file=sys.stderr)
            return 1
        graph, _ = stored_graph(db, plugins)
        return plugin_commands[args.command](e.ctx, graph, args)
    if args.command == "serve":
        supervisor.serve_supervised()
        port = args.port or config.port
        if args.open:
            _open(auth.login_url(port, token, scheme(config)))
        serve(config, db, token, port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
