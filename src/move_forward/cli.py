"""The `mfd` command."""

import argparse
import errno
import getpass
import io
import json
import logging
import os
import shlex
import subprocess
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
)
from . import config as config_module
from .act_now import PRIORITIES, UNTIL_CHANGED
from .db import Database
from .graph import Ref
from .kinds import label
from .plugin import CliHandler, InputRequest
from .refresh import (
    build_dashboard,
    refresh,
    refresh_subjects,
    refreshing,
    snooze_item,
    snoozed,
    stored_graph,
)
from .registry import enabled, enabled_names, migrate
from .repositories import (
    ContactRepository,
    FeedbackRepository,
    PriorityRepository,
    SessionRepository,
    TagRepository,
)
from .server import serve
from .shell import CommandError


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
        "snooze",
        help="snooze the items about an entity, as the Snooze button does, until each changes"
        " (or the time is up)",
    )
    snooze_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    snooze_parser.add_argument(
        "duration",
        nargs="?",
        help=f"e.g. 7d or 12h, or {UNTIL_CHANGED} for until it changes; default: as the"
        " button, [act_now] snooze_days for its kind",
    )
    snooze_parser.add_argument("--kind", help="the entity's kind, e.g. notes.todo")
    snooze_parser.add_argument("--task", help="only this task of its, e.g. review")
    priority_parser = commands.add_parser(
        "priority",
        help="set how much an entity matters: 1 important, 2 critical, or clear",
    )
    priority_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    priority_parser.add_argument("level", choices=["1", "2", "clear"])
    priority_parser.add_argument("--kind", help="the entity's kind, e.g. notes.todo")
    tag_parser = commands.add_parser("tag", help="put a #tag on an entity, or take one off it")
    tag_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    tag_parser.add_argument("tag", help="e.g. garbage-day or #garbage-day")
    tag_parser.add_argument("--kind", help="the entity's kind, e.g. mail.thread")
    tag_parser.add_argument(
        "--remove",
        action="store_true",
        help="take the tag off: one from a tool or text comes back at its plugin's next refresh",
    )
    contact_parser = commands.add_parser(
        "contact", help="set what someone is to me, e.g. a customer, or clear it"
    )
    contact_parser.add_argument("who", help="an email address, or @domain for everyone there")
    contact_parser.add_argument("type", help="e.g. customer, or clear")
    commands.add_parser("contacts", help="print the contact types I set as JSON: who, contact_type")
    unsnooze_parser = commands.add_parser(
        "unsnooze", help="bring back what's snoozed about an entity"
    )
    unsnooze_parser.add_argument(
        "entity", help="a PR's URL, a ticket's key, an entry's title, or (with --kind) an id"
    )
    unsnooze_parser.add_argument("--kind", help="the entity's kind, e.g. mail.thread")
    unsnooze_parser.add_argument("--task", help="only this task of its, e.g. review")
    commands.add_parser(
        "snoozes",
        help="print the Act now items snoozed now (not yet over, and unchanged since), including"
        " items on ice, which the page hides, as JSON: kind, id, task, until (null: until it"
        " changes), fingerprint",
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


def _open(url: str) -> None:
    """Open the dashboard in a browser; print the URL when there's no browser to open."""
    if not webbrowser.open(url):
        print(f"open {url} in a browser to sign in")


def _open_signed_in(token: str, port: int) -> int:
    """Open the running dashboard in a browser, signed in with a one-time code it gives once
    it has proved it knows the token (see `signin`). Returns the exit status."""
    try:
        code = signin.login_code(port, token)
    except signin.SignInError as e:
        print(e, file=sys.stderr)
        return 1
    _open(auth.login_url(port, code))
    return 0


def command_line(words: list[str]) -> str:
    """`words` as a command to paste into this system's shell, each argument kept whole
    whatever it holds (an entry's id is its title): quoted for a POSIX shell, or on Windows
    as its programs split their command line, though cmd and PowerShell still read their own
    characters inside it (cmd's `&` and `%`; PowerShell's `$` and backtick, and it doesn't
    take a backslash before a quote as escaping it)."""
    return subprocess.list2cmdline(words) if sys.platform == "win32" else shlex.join(words)


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
            config_path.write_text(init.example(), encoding="utf-8")
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
        return _open_signed_in(token, args.port or config.port)
    db = Database(config.db_path, config.db_backups)
    migrate(config, db)

    if args.command == "logout":
        print(f"ended {SessionRepository(db).revoke_all()} session(s)")
        return 0
    if args.command == "refresh":
        if args.subject:
            refs = [Ref(kind=kind, id=id) for kind, id in args.subject]
            refresh_subjects(config, db, refs)
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
        now = datetime.now(UTC)
        try:
            until_changed = args.duration == UNTIL_CHANGED
            duration = (
                snooze.parse_duration(args.duration)
                if args.duration and not until_changed
                else None
            )
            graph, _ = stored_graph(db, enabled(config, db, now))
            ref = priority.resolve(graph, args.entity, args.kind)
        except (ValueError, LookupError) as err:
            print(err, file=sys.stderr)
            return 1
        buttons = snooze.snooze_buttons(build_dashboard(config, db, now), ref, args.task)
        if not buttons:
            words = ["mfd", "unsnooze", ref.id, "--kind", ref.kind]
            words += ["--task", args.task] if args.task else []
            again = command_line(words)
            print(
                f"nothing about {ref.id} is shown with a Snooze button: it may be snoozed"
                f" already (`{again}` first), or not shown at all",
                file=sys.stderr,
            )
            return 1
        ends = [snooze_item(config.act_now, db, f, now, duration, until_changed) for f in buttons]
        timed = [end for end in ends if end is not None]
        if not timed:
            print(f"snoozed {len(buttons)} item(s) until each changes")
            return 0
        until = f"{localtime.local(max(timed)):%Y-%m-%d %H:%M}"
        print(f"snoozed {len(buttons)} item(s) until {until}, or until each changes")
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
        plugins = enabled(config, db, datetime.now(UTC))
        graph, _ = stored_graph(db, plugins)
        try:
            ref = priority.resolve(graph, args.entity, args.kind)
            tag = tags.tag_key(args.tag)
        except (LookupError, ValueError) as err:
            print(err, file=sys.stderr)
            return 1
        repo = TagRepository(db)
        if args.remove:
            taken = tags.take_off(repo, graph, ref, tag, datetime.now(UTC))
            back = taken.back(refreshing(plugins))
            print(f"#{tag} taken off {ref.id}{back}" if taken.on else f"{ref.id} had no #{tag}")
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
                "until": f.until.isoformat() if f.until else None,
                "fingerprint": f.fingerprint,
            }
            for (kind, id, task), f in snoozed(config, db, now).items()
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
        return plugin_commands[args.command](InputRequest(e.ctx, graph, args=args))
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
                on_listening=(lambda: _open_signed_in(token, port)) if args.open else None,
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
