"""The `mfd` command."""

import argparse
import errno
import getpass
import io
import json
import logging
import os
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from . import (
    auth,
    init,
    keychain,
    localtime,
    people,
    priority,
    registry,
    signin,
    snooze,
    supervisor,
    tags,
    tls,
)
from . import config as config_module
from .act_now import PRIORITIES
from .db import Database
from .graph import Ref
from .kinds import label
from .plugin import CliHandler, CliRequest
from .refresh import build_dashboard, refresh, refresh_subjects, stored_graph
from .registry import enabled, enabled_names, migrate
from .repositories import (
    ContactRepository,
    FeedbackRepository,
    PriorityRepository,
    SessionRepository,
    TagRepository,
)
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
# host = "github.com"                    # a GitHub Enterprise Server's host instead
# refresh_seconds = 300
# search_scope = "org"                 # my PR searches: "org" (this org's repos) or "everywhere"
# history_days = 14
# policy_checks = []                     # checks that enforce PR policy, not CI
# ignored_reviewers = []                 # logins whose comments aren't review feedback
# primary_reviewer_repos = []            # repos whose PRs have a primary reviewer as an assignee
# required_owner_marker = ""             # CODEOWNERS comment marking required owners
# ticket_link_prefixes = []             # PR body lines that link the ticket start with one, e.g. ["Link", "Closes"]
# placeholder_labels = []                # template labels to replace, e.g. ["needs-team"]
# default_team_reviewer_assignment = "team" # team requests count without an individual
# team_reviewer_assignment = {}          # by slug, e.g. { bacon = "individual" }
# email_domains = []                     # reviewers' email domains, if not just your email's
# stale_review_hours = 24
# merge_queue_eviction_days = 2        # how long a merge-queue removal stays in CI failures
# stale_draft_days = 14                # my drafts untouched this long get a housekeeping line (0: off)
# quick_review_max_lines = 100           # a review within these limits takes minutes
# quick_review_max_files = 5
# hour_review_max_lines = 300            # within these, an hour; beyond, a day
# hour_review_max_files = 12
# split_pr_suggest_lines = 600           # my PRs this big "may need splitting"
# split_pr_lines = 800                   # ...and this big are "worth splitting"
# split_pr_labels = []                   # labels that mean worth splitting, e.g. ["size/XL"]
# reviewer_history_days = 180

# [github.merge]
# method = "squash"                     # where a repo allows several; else GitHub's pick for me

# [github.teams]                        # a team's slug, where it isn't the team's name
# my-team = "my-team-reviewers"

[git]
repos = ["~/src/app", "~/src/infra"]  # main checkouts; other worktrees are found from them
# refresh_seconds = 1800
# status_seconds = 30
# my_branch_prefixes = ["me/"]            # besides branches with my commits or PRs
# ignored_worktree_globs = ["*/.claude/worktrees/*"]
# commit_history_days = 180
# github_hosts = ["github.com"]           # remote hosts that are GitHub: an Enterprise server, ~/.ssh/config aliases

[git_stack]                           # stacks from git-stack, in [git]'s repos
# refresh_seconds = 1800                 # how often it refreshes regardless of git's changes

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

# [mail]                                # unread and flagged mail, over IMAP; the password: `mfd token set mail`
# host = "imap.gmail.com"
# username = "me@example.com"
# message_url = "https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}"  # a link to a message
# refresh_seconds = 900
# port = 993
# mailbox = "INBOX"
# sent_mailbox = ""                     # where my sent mail is; empty: the one the server marks sent
# known_days = 90                       # my sent mail read this far back: whom I know
# urgent_words = ["outage"]             # in a subject, critical, from someone I know
# low_words = ["digest"]                # in a subject, ranked with lists' mail, not from someone I know
# since_days = 14                       # older mail is left out, flagged or not
# max_messages = 200                    # the most unread read; flagged: all
# auth_servers = ["mx.google.com"]      # who stamps Authentication-Results (known for Gmail); senders are verified by them
# token_env = "MAIL_PASSWORD"           # else the system's secret store, for my email
# keychain_service = "move-forward-mail"
## Several mailboxes instead: no username above, but a table each, taking from [mail] the rest
## [mail.home]
## username = "me@example.com"          # its password: `mfd token set mail.home`
## [mail.work]
## host = "imap.work.example"
## username = "me@work.example"         # its password: `mfd token set mail.work`

# [vcard]                               # an address book's people, from vCard files
# paths = ["~/.contacts"]               # vCard files, or directories of them (vdirsyncer's)
# refresh_seconds = 3600
# group_importance = {Family = 2}       # how much a group's people matter (0-2)
# group_types = {Clients = "customer"}  # what a group's people are to me

# [jira]
# site = "https://example.atlassian.net"
# refresh_seconds = 900
# token_env = "JIRA_API_TOKEN"            # else the system's secret store, for my email
# keychain_service = "move-forward-jira"
# status_in_progress = ["In Progress"]    # workflow names the transitions panel uses; a ticket matches any, a move goes to the first it can
# status_in_review = ["In Review"]        # e.g. ["In Review", "Code Review"]
# status_closed = ["Closed"]
# status_blocked = ["Blocked"]           # tickets in it count as blocked, with or without blocker links
# status_rollout = ["Rollout"]         # merged, waiting on monitoring: never proposed for closing until its PRs merge (empty: off)
# blocked_by_links = []                  # more link names meaning "blocked by", e.g. ["depends on"]
# escalation_label_prefix = ""            # labels that rank incidents, e.g. "escalated-to-"
# escalation_levels = []                  # what follows it, lowest first, e.g. ["manager", "director", "vp"]
# priorities = ["Lowest", "Low", "Medium", "High", "Highest"]  # Jira's priority names, lowest first
# incident_types = ["incident"]           # issue types (substrings, lower-case)
# bug_types = ["bug"]
## [jira.teams]                         # a team's id in Jira's team field, by its name in teams (unused for now)
## my-team = "10042"

# [people]                              # what people are to me
# contact_types = { customer = 1, partner = 1, vendor = 0 }  # the types offered, each with the importance it gives (0 to 2)

# [act_now]                             # how Act now ranks what the plugins nominate
# size = 7                               # items shown
# per_plugin = 3                         # so one busy tool can't fill the list
# weights = { closes = 3, minutes = 2, hour = 1, waiting = 2, importance = 3, due = 1, blocked = -4 }  # what each signal adds
# blocked_stale_days = 3                 # a blocked item untouched this long stops being ranked down (0: never)
# due_warning_days = 7                  # a due date starts raising an item this many days ahead
# default_snooze_days = 1                # days a snooze lasts (1 = until tomorrow)
# snooze_until_hour = 0                # the local hour a snooze ends at on its last day (9: 9am)
# snooze_days = { pull_request = 7 }     # ...for a kind of subject, e.g. a PR's review for a week

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
    commands.add_parser(
        "logout", help="sign every browser out: end all sessions (`mfd open` signs one in again)"
    )
    commands.add_parser("restart", help="ask a running mfd serve to restart its server process")
    refresh_parser = commands.add_parser("refresh", help="refresh every source once and exit")
    refresh_parser.add_argument(
        "--subject",
        nargs=2,
        action="append",
        metavar=("KIND", "ID"),
        help="fetch just this entity again, e.g. pull_request <URL>; may be repeated",
    )
    commands.add_parser(
        "check", help="load the config and every plugin, and exit; fails with what's wrong"
    )
    commands.add_parser("dump", help="print the dashboard as JSON from what is stored")
    snooze_parser = commands.add_parser(
        "snooze", help="snooze a PR's review until it changes (or the time given is up)"
    )
    snooze_parser.add_argument("url", help="the PR's URL")
    snooze_parser.add_argument(
        "duration", nargs="?", help="e.g. 7d or 12h; default: [act_now] snooze_days for PRs"
    )
    priority_parser = commands.add_parser(
        "priority",
        help="set how much an entity matters: 0 not important, 1 important, 2 critical, or clear",
    )
    priority_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    priority_parser.add_argument("level", choices=["0", "1", "2", "clear"])
    priority_parser.add_argument("--kind", help="the entity's kind, e.g. notes.todo")
    tag_parser = commands.add_parser(
        "tag", help="put a #tag on an entity, or take one I put there off"
    )
    tag_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    tag_parser.add_argument("tag", help="e.g. garbage-day or #garbage-day")
    tag_parser.add_argument("--kind", help="the entity's kind, e.g. mail.thread")
    tag_parser.add_argument("--remove", action="store_true", help="take the tag off")
    contact_parser = commands.add_parser(
        "contact", help="set what someone is to me, e.g. a customer, or clear it"
    )
    contact_parser.add_argument("who", help="an email address, or @domain for everyone there")
    contact_parser.add_argument("type", help="e.g. customer, or clear")
    commands.add_parser("contacts", help="print the contact types I set as JSON: who, contact_type")
    unsnooze_parser = commands.add_parser(
        "unsnooze", help="bring back what's snoozed or dismissed about an entity"
    )
    unsnooze_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    unsnooze_parser.add_argument("--kind", help="the entity's kind, e.g. mail.thread")
    unsnooze_parser.add_argument("--task", help="only this task of its, e.g. review")
    commands.add_parser(
        "snoozes",
        help="print the Act now items snoozed now as JSON: kind, id, task, until, fingerprint",
    )
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
    set_parser.add_argument(
        "plugin",
        help="the plugin whose token to store, e.g. jira, or one of its accounts: mail.work",
    )
    token_commands.add_parser("check", help="say where each plugin's tokens are found")
    token_commands.add_parser(
        "rotate", help="make a new dashboard token and sign every browser out"
    )
    plugin_commands = {
        plugin.name: plugin.cli(commands.add_parser(plugin.name, help=plugin.cli_help))
        for plugin in registry.PLUGINS
        if plugin.cli_help
    }
    return parser, plugin_commands


def _token(config: config_module.Config, config_path: Path, args: argparse.Namespace) -> int:
    """`mfd token set <plugin>` (or `<plugin>.<account>`): ask for the token without echoing
    it and store it in the system's secret store, for the top-level `email`. `mfd token
    check`: say where each enabled plugin's tokens are found, without showing them."""
    tokens = registry.tokens(config)
    if args.token_command == "rotate":
        path = auth.token_path(config.db_path)
        auth.load_or_create(path)
        auth.rotate(path)
        ended = SessionRepository(Database(config.db_path, config.db_backups)).revoke_all()
        print(f"new token in {path}; ended {ended} session(s): `mfd open` signs a browser in")
        return 0
    if args.token_command == "check":
        for name, settings in tokens.items():
            if os.environ.get(settings.token_env):
                print(f"{name}: from ${settings.token_env}")
            elif keychain.read(settings.keychain_service, config.email):
                print(f"{name}: from the secret store ({settings.keychain_service})")
            else:
                print(f"{name}: missing; run `mfd token set {name}`")
        return 0
    settings = tokens.get(args.plugin)
    if settings is None:
        print(
            f"{args.plugin} isn't a token {config_path} keeps: {', '.join(tokens) or 'none'}",
            file=sys.stderr,
        )
        return 1
    try:
        secret = getpass.getpass(f"{args.plugin} API token: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nnothing stored", file=sys.stderr)
        return 1
    try:
        keychain.write(settings.keychain_service, config.email, secret)
    except FileNotFoundError:
        print(
            f"no secret store: install `secret-tool` (libsecret), or set ${settings.token_env}",
            file=sys.stderr,
        )
        return 1
    except (ValueError, CommandError) as e:
        print(f"not stored: {e}", file=sys.stderr)
        return 1
    print(f"stored under {settings.keychain_service} for {config.email}")
    if os.environ.get(settings.token_env):
        print(f"note: ${settings.token_env} is set, and is used before the secret store")
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


def _open_signed_in(config: config_module.Config, token: str, port: int) -> int:
    """Open the running dashboard in a browser, signed in with a one-time code it gives once
    it has proved it knows the token (see `signin`). Returns the exit status."""
    served = scheme(config)
    ca = tls.Paths.beside(config.db_path).ca
    try:
        code = signin.login_code(port, served, token, ca if served == "https" else None)
    except signin.SignInError as e:
        print(e, file=sys.stderr)
        return 1
    _open(auth.login_url(port, code, served))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Printed as UTF-8, redirected or not: Windows' default is its code page, which can't
        # write an emoji in a note's title, and would fail after the command did its work.
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
            config_path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
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
        for error in registry.DISCOVERY_ERRORS:
            print(f"warning: {error}")
        if checked.https and (problem := tls.problem(tls.Paths.beside(checked.db_path))):
            print(f"warning: {problem}")
        return 0
    config = config_module.load(config_path)
    if args.command == "restart":
        supervisor.request_restart(config_path)
        print("server restart requested")
        return 0
    if args.command == "token":
        return _token(config, config_path, args)
    if args.command == "serve" and not supervisor.supervised():
        # The server runs in a child process, which this one replaces on a change (see
        # `supervisor`); the child opens the database and serves.
        full_argv = [sys.argv[0], *(sys.argv[1:] if argv is None else argv)]
        return supervisor.supervise(full_argv, config_path, code=args.reload)
    token = auth.load_or_create(auth.token_path(config.db_path))
    if args.command == "open":
        return _open_signed_in(config, token, args.port or config.port)
    if args.command == "cert":
        return _cert(tls.Paths.beside(config.db_path), args.force, args.trust)
    db = Database(config.db_path, config.db_backups)
    migrate(config, db)

    if args.command == "logout":
        print(f"ended {SessionRepository(db).revoke_all()} session(s)")
        return 0
    if args.command == "refresh":
        if args.subject:
            refs = [Ref(kind=kind, id=id) for kind, id in args.subject]
            if fallback := refresh_subjects(config, db, refs):
                refresh(config, db, None, fallback)
        else:
            refresh(config, db)
        dashboard = build_dashboard(config, db)
        for source, status in dashboard.sources.items():
            print(f"{source}: {'error: ' + status.error if status.error else 'ok'}")
        return 0
    if args.command == "dump":
        print(build_dashboard(config, db).model_dump_json(indent=2))
        return 0
    if args.command == "snooze":
        try:
            duration = snooze.parse_duration(args.duration) if args.duration else None
            plugins = enabled(config, db, datetime.now(UTC))
            graph, _ = stored_graph(db, plugins)
            until = snooze.snooze_review(
                FeedbackRepository(db), graph, config.act_now, args.url, datetime.now(UTC), duration
            )
        except (ValueError, LookupError) as err:
            print(err, file=sys.stderr)
            return 1
        print(f"snoozed until {localtime.local(until):%Y-%m-%d %H:%M}, or until the PR changes")
        return 0
    if args.command == "priority":
        graph, _ = stored_graph(db, enabled(config, db, datetime.now(UTC)))
        try:
            ref = priority.resolve(graph, args.entity, args.kind)
        except LookupError as err:
            print(err, file=sys.stderr)
            return 1
        priorities = PriorityRepository(db)
        if args.level == "clear":
            priorities.clear(ref)
            print(f"priority of {label(ref.kind)} {ref.id} cleared")
        else:
            priorities.set(ref, int(args.level), datetime.now(UTC))
            print(f"{label(ref.kind)} {ref.id}: {PRIORITIES[int(args.level)].lower()}")
        return 0
    if args.command == "tag":
        graph, _ = stored_graph(db, enabled(config, db, datetime.now(UTC)))
        try:
            ref = priority.resolve(graph, args.entity, args.kind)
            tag = tags.tag_key(args.tag)
        except (LookupError, ValueError) as err:
            print(err, file=sys.stderr)
            return 1
        repo = TagRepository(db)
        if args.remove:
            removed = repo.remove(ref, tag)
            print(f"#{tag} taken off {ref.id}" if removed else f"{ref.id} had no #{tag} of mine")
        else:
            added = repo.add(ref, tag, datetime.now(UTC))
            print(f"{ref.id}: #{tag}" if added else f"{ref.id} has #{tag} already")
        return 0
    if args.command == "contact":
        contacts = ContactRepository(db)
        try:
            if args.type == "clear":
                who, kept = people.clear_label(args.who, contacts.clear)
                print(f"{who}: type cleared" if kept else f"{who} had no type")
            else:
                who = people.contact_key(args.who)
                contact_type = people.contact_type(args.type)
                contacts.set(who, contact_type, datetime.now(UTC))
                print(f"{who}: {contact_type}")
        except ValueError as err:
            print(err, file=sys.stderr)
            return 1
        return 0
    if args.command == "contacts":
        rows = [
            {"who": who, "contact_type": contact_type}
            for who, contact_type in ContactRepository(db).all().items()
        ]
        print(json.dumps(rows, indent=2))
        return 0
    if args.command == "unsnooze":
        graph, _ = stored_graph(db, enabled(config, db, datetime.now(UTC)))
        try:
            ref = priority.resolve(graph, args.entity, args.kind)
        except LookupError as err:
            print(err, file=sys.stderr)
            return 1
        back = FeedbackRepository(db).bring_back(ref.kind, ref.id, args.task, datetime.now(UTC))
        print(f"brought back {back} item(s)" if back else f"nothing of {ref.id} is set aside")
        return 0
    if args.command == "snoozes":
        now = datetime.now(UTC)
        snoozes = [
            {
                "kind": kind,
                "id": id,
                "task": task,
                "until": f.until.isoformat(),
                "fingerprint": f.fingerprint,
            }
            for (kind, id, task), f in FeedbackRepository(db).all().items()
            if f.action == "snooze" and f.until is not None and f.until > now
        ]
        print(json.dumps(snoozes, indent=2))
        return 0
    if args.command in plugin_commands:
        plugins = enabled(config, db, datetime.now(UTC))
        e = next((e for e in plugins if e.name == args.command), None)
        if e is None:
            print(f"[{args.command}] isn't in {config_path}", file=sys.stderr)
            return 1
        graph, _ = stored_graph(db, plugins)
        return plugin_commands[args.command](CliRequest(e.ctx, graph, args))
    if args.command == "serve":
        supervisor.serve_supervised()
        port = args.port or config.port
        try:
            serve(
                config,
                db,
                token,
                port,
                config_path,
                on_listening=(lambda: _open_signed_in(config, token, port)) if args.open else None,
            )
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            print(
                f"port {port} is already in use; run `mfd open` for the existing dashboard "
                "or choose another port with `mfd serve --port N`",
                file=sys.stderr,
            )
            return supervisor.PORT_IN_USE_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
