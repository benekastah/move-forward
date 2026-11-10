"""`mfd notes`: read and change the notes.

Every command works without the server, straight on the database, and prints `--json` for
scripts (the move-forward skill calls them). Changes go through the same functions as the
dashboard's buttons (`actions`), so both check the same things.
"""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from ...api import (
    ActionError,
    ActionResult,
    CliHandler,
    Context,
    Form,
    Graph,
    InputRequest,
)
from ...api.kinds import TICKET
from . import actions, panels
from .config import NotesConfig
from .models import Condition, Entry, Ice, Size, Todo
from .repository import NotesRepository
from .text import PR_URL_RE, links_in, sized

Command = Callable[[InputRequest[NotesConfig]], "Result"]


class Result:
    """What a command did: a line to print and, with `--json`, data to print instead."""

    def __init__(self, text: str, data: object = None):
        self.text = text
        self.data = data if data is not None else {"notice": text}


def find_entry(entries: list[Entry], ref: str, graph: Graph) -> Entry:
    """The entry `ref` names: by id or title, else by a PR URL or ticket key it's about."""
    ref = ref.strip()
    ticket = graph.identify(TICKET, ref)
    found = [e for e in entries if str(e.id) == ref or e.title == ref]
    found = found or [
        e
        for e in entries
        if any(link.ref in (ref, ticket) for link in e.links)
        or ticket in panels.entry_tickets(e, graph)
    ]
    if not found:
        raise ValueError(f"no entry {ref!r}")
    if len(found) > 1:
        raise ValueError(
            f"{ref!r} is about {len(found)} entries: {', '.join(e.title for e in found)}"
        )
    return found[0]


def _entry_line(entry: Entry) -> str:
    open_todos = len(entry.open_todos)
    status = f"  [{entry.status}]" if entry.status else ""
    blocked = f"  blocked: {entry.blocked_on}" if entry.blocked_on else ""
    return f"{entry.id}  {entry.title}{status}{blocked}  ({open_todos} open)"


def _todo_line(todo: Todo) -> str:
    mark = f"done {todo.done_on}" if todo.done_on else (todo.size or "open")
    links = "".join(f"  {link.ref}" for link in todo.links)
    return f"  #{todo.id} [{mark}] {todo.text}{links}"


def _entry_text(entry: Entry) -> str:
    lines = [_entry_line(entry)]
    if entry.where:
        lines.append(f"  where: {entry.where}")
    lines += [f"  about: {link.ref}" for link in entry.links]
    lines += [f"  merge condition on {c.pr}: {c.text}" for c in entry.conditions]
    lines += [_todo_line(todo) for todo in entry.todos]
    return "\n".join(lines)


def _ice_text(ice: Ice) -> str:
    items = ", ".join(item.name for item in ice.items)
    note = f"  ({ice.note})" if ice.note else ""
    return f"{ice.id}  {ice.title}  iced {ice.iced_on}: {items}{note}"


def _entries(ctx: Context[NotesConfig]) -> list[Entry]:
    return NotesRepository(ctx.db).entries()


def _notice(result: str | ActionResult) -> str:
    return result if isinstance(result, str) else result.notice


def cmd_list(req: InputRequest[NotesConfig]) -> Result:
    ctx, args = req.ctx, req.args
    entries = [e for e in _entries(ctx) if e.open_todos or not args.open]
    text = "\n".join(_entry_line(e) for e in entries)
    return Result(text, [e.model_dump(mode="json") for e in entries])


def cmd_show(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    entry = find_entry(_entries(ctx), args.entry, graph)
    return Result(_entry_text(entry), entry.model_dump(mode="json"))


def cmd_ices(req: InputRequest[NotesConfig]) -> Result:
    ctx = req.ctx
    ices = NotesRepository(ctx.db).ices()
    return Result("\n".join(_ice_text(i) for i in ices), [i.model_dump(mode="json") for i in ices])


def cmd_add_entry(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    notes = NotesRepository(ctx.db)
    try:
        title = actions.entry_title(args.title.strip())  # as the dashboard's buttons check it
    except ActionError as e:
        raise ValueError(str(e)) from e
    if notes.entry_id(title) is not None:
        raise ValueError(f"there is already an entry titled {title!r}")
    links = [actions.link_for(graph, v) for v in args.link]
    entry_id = notes.add_entry(
        title, ctx.today, args.status, args.where, list(dict.fromkeys(links))
    )
    return Result(f"added entry {entry_id}", {"notice": f"added {title}", "id": entry_id})


def cmd_remove(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry, graph)
    notes.delete_entry(entry.id)
    return Result(f"removed {entry.title}")


def cmd_set(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry, graph)
    if args.field == "status":
        notes.set_status(entry.id, args.text)
    elif args.field == "where":
        notes.set_where(entry.id, args.text)
    else:
        notes.set_blocked_on(entry.id, args.text)
    notes.touch(entry.id, ctx.today)
    return Result(f"{entry.title}: {args.field} is now {args.text}")


def cmd_link(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry, graph)
    link = actions.link_for(graph, args.ref)
    if not notes.link_entry(entry.id, link):
        raise ValueError(f"{entry.title} is already about {args.ref}")
    notes.touch(entry.id, ctx.today)
    return Result(f"{entry.title}: linked {args.ref}")


def cmd_todo_add(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry, graph)
    text, written = sized(args.text.strip())  # as the notes write a size
    text = text.strip()
    if not text:
        raise ValueError("the todo is empty")
    links = [*links_in(text), *(actions.link_for(graph, v) for v in args.link)]
    size: Size | None = args.size or written
    todo_id = notes.add_todo(entry.id, text, size, list(dict.fromkeys(links)))
    notes.touch(entry.id, ctx.today)
    return Result(f"added todo {todo_id}", {"notice": f"added: {text}", "id": todo_id})


def cmd_todo(req: InputRequest[NotesConfig]) -> Result:
    """`todo add`, or done, undone, delete or link on the todo `args.todo` by id."""
    ctx, graph, args = req.ctx, req.graph, req.args
    if args.todo_command == "add":
        return cmd_todo_add(req)
    handlers = {
        "done": actions.todo_done,
        "undone": actions.todo_undone,
        "delete": actions.todo_delete,
        "link": actions.todo_link,
    }
    fields = {"todo": str(args.todo)}
    if args.todo_command == "link":
        fields["input"] = args.ref
    return Result(_notice(handlers[args.todo_command](InputRequest(ctx, graph, form=Form(fields)))))


def cmd_condition(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry, graph)
    text = args.text.strip()
    pr = args.pr.strip() if args.pr else None
    if pr is not None and not PR_URL_RE.fullmatch(pr):
        raise ValueError(f"{pr!r} isn't a pull request's URL")
    if args.condition_command == "add":
        if pr is None:
            raise ValueError("a merge condition holds one PR: name it with --pr")
        if not notes.add_condition(entry.id, Condition(pr=pr, text=text)):
            raise ValueError(f"{entry.title} already has that merge condition on {pr}")
    elif not notes.remove_condition(entry.id, text, pr):
        raise ValueError(f"{entry.title} has no merge condition {text!r}")
    notes.touch(entry.id, ctx.today)
    return Result(f"{entry.title}: {args.condition_command} merge condition")


def cmd_remember(req: InputRequest[NotesConfig]) -> Result:
    ctx, args = req.ctx, req.args
    key = _key(args.key)
    on = date.fromisoformat(args.on) if args.on else ctx.today
    NotesRepository(ctx.db).remember(key, on)
    return Result(f"remembered {key} on {on}", {"key": key, "on": on.isoformat()})


def cmd_recall(req: InputRequest[NotesConfig]) -> Result:
    ctx, args = req.ctx, req.args
    key = _key(args.key)
    on = NotesRepository(ctx.db).recall(key)
    return Result(on.isoformat() if on else "", {"key": key, "on": on.isoformat() if on else None})


def _key(key: str) -> str:
    """`key` without the space around it (a shell substitution's newline, say); otherwise it's
    matched exactly."""
    if not (stripped := key.strip()):
        raise ValueError("the key is empty")
    return stripped


def cmd_ice(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    if args.item:
        kind, ref_id = args.item
        fields = {"kind": kind, "id": ref_id, "note": args.note or ""}
        return Result(_notice(actions.ice_item(InputRequest(ctx, graph, form=Form(fields)))))
    repo, branch = args.branch
    fields = {"repo": str(Path(repo).expanduser()), "branch": branch, "note": args.note or ""}
    return Result(_notice(actions.ice(InputRequest(ctx, graph, form=Form(fields)))))


def cmd_thaw(req: InputRequest[NotesConfig]) -> Result:
    ctx, graph, args = req.ctx, req.graph, req.args
    return Result(
        _notice(actions.thaw(InputRequest(ctx, graph, form=Form({"ice": str(args.ice)}))))
    )


def _commands() -> dict[str, Command]:
    return {
        "list": cmd_list,
        "show": cmd_show,
        "ices": cmd_ices,
        "add-entry": cmd_add_entry,
        "remove": cmd_remove,
        "set": cmd_set,
        "link": cmd_link,
        "todo": cmd_todo,
        "condition": cmd_condition,
        "remember": cmd_remember,
        "recall": cmd_recall,
        "ice": cmd_ice,
        "thaw": cmd_thaw,
    }


def add_commands(parser: argparse.ArgumentParser) -> CliHandler:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON, for scripts")
    commands = parser.add_subparsers(dest="notes_command", required=True)

    def add(name: str, help: str) -> argparse.ArgumentParser:
        return commands.add_parser(name, help=help, parents=[common])

    add("list", "entries with their todos").add_argument(
        "--open", action="store_true", help="only entries with open todos"
    )
    show = add("show", "one entry, by id, title, PR URL or ticket key")
    show.add_argument("entry")
    add("ices", "what's on ice, with the ids `thaw` takes")
    add_entry = add("add-entry", "add an entry")
    add_entry.add_argument("title")
    add_entry.add_argument("--link", action="append", default=[], help="a PR URL or ticket key")
    add_entry.add_argument("--status")
    add_entry.add_argument("--where", help="where the work lives: repo, branches, PRs")
    add("remove", "remove an entry with its todos, for finished work").add_argument("entry")
    set_parser = add("set", "set an entry's status, where it lives, or what it's blocked on")
    set_parser.add_argument("entry")
    set_parser.add_argument("field", choices=["status", "where", "blocked-on"])
    set_parser.add_argument("text")
    link = add("link", "say an entry is about a PR or ticket")
    link.add_argument("entry")
    link.add_argument("ref", help="a PR URL or ticket key")

    todo = commands.add_parser(  # `--json` goes on its leaves, not here, or it would be reset
        "todo", help="add a todo to an entry, or tick off, reopen, drop or link one by id"
    )
    todo_commands = todo.add_subparsers(dest="todo_command", required=True)
    todo_add = todo_commands.add_parser("add", help="add a todo", parents=[common])
    todo_add.add_argument("entry")
    todo_add.add_argument("text")
    todo_add.add_argument("--size", choices=["quick", "big"])
    todo_add.add_argument("--link", action="append", default=[], help="a PR URL or ticket key")
    for name in ("done", "undone", "delete"):
        todo_commands.add_parser(name, parents=[common]).add_argument("todo", type=int)
    todo_link = todo_commands.add_parser("link", parents=[common])
    todo_link.add_argument("todo", type=int)
    todo_link.add_argument("ref", help="a PR URL or ticket key")

    condition = add("condition", "add or remove a merge condition")
    condition.add_argument("condition_command", choices=["add", "remove"])
    condition.add_argument("entry")
    condition.add_argument("text")
    condition.add_argument(
        "--pr", help="the PR it holds, by URL (needed to add; to remove, default every PR's)"
    )
    remember = add("remember", "record that something happened, by a key of the skill's")
    remember.add_argument(
        "key", help="what happened, e.g. checked:<entry title>; recall matches it exactly"
    )
    remember.add_argument("--on", help="the day, YYYY-MM-DD (default: today)")
    add("recall", "the day `remember` last recorded for a key, or nothing").add_argument("key")
    ice = add(
        "ice",
        "put a branch (with its stack in git stack), a PR, a ticket or a work entry on ice"
        " (needs a refresh first)",
    )
    what = ice.add_mutually_exclusive_group(required=True)
    what.add_argument("--branch", nargs=2, metavar=("REPO", "BRANCH"))
    what.add_argument(
        "--item",
        nargs=2,
        metavar=("KIND", "ID"),
        help="pull_request URL, ticket KEY or notes.entry TITLE",
    )
    ice.add_argument("--note")
    add("thaw", "take what's on ice off it").add_argument("ice", type=int)
    handlers = _commands()

    def run(req: InputRequest[NotesConfig]) -> int:
        args = req.args
        try:
            result = handlers[args.notes_command](req)
        except (ValueError, ActionError) as e:
            print(f"mfd notes {args.notes_command}: {e}", file=sys.stderr)
            return 1
        print(json.dumps(result.data, indent=2) if args.json else result.text)
        return 0

    return run
