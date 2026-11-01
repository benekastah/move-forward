"""`mfd notes`: read and change the notes, and import or export the skill's markdown.

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

from ... import notes as skill_notes
from ...graph import Graph
from ...plugin import ActionError, ActionResult, CliHandler, Context
from ...repositories import ReviewerRepository
from . import actions, markdown
from .config import NotesConfig
from .models import Entry, Ice, Size, Todo
from .repository import NotesRepository

LAST_ASKED_ICE = "last_asked_ice"
"""The kv key for when the skill last asked about what's on ice."""


def import_notes(ctx: Context[NotesConfig], notes_dir: Path, replace: bool) -> str:
    """Read `work-notes.md`, `on-ice.md` and `reviewer-map.md` from `notes_dir` into the
    tables. Refuses to replace notes already there unless `replace`. The reviewer map and
    the skill's nudges go to core's tables, where slack reads them, and can be imported
    again at any time."""

    def read(name: str) -> str:
        path = notes_dir / name
        return path.read_text() if path.exists() else ""

    repo = NotesRepository(ctx.db)
    work_notes = read(markdown.WORK_NOTES_FILE)
    entries, sections = markdown.read_work_notes(work_notes)
    ices, last_asked = markdown.read_on_ice(read(markdown.ON_ICE_FILE), ctx.now.date())
    if (entries or ices) and not repo.is_empty() and not replace:
        raise ValueError("the notes already have entries; pass --replace to replace them")
    if entries or ices or sections:
        repo.replace_all(entries, ices, sections)
    if last_asked:
        ctx.kv.set(LAST_ASKED_ICE, last_asked.isoformat())
    users, teams = skill_notes.parse_reviewer_map(read(skill_notes.REVIEWER_MAP_FILE))
    ReviewerRepository(ctx.db).replace_imported(users, teams)
    return (
        f"imported {len(entries)} entries, {sum(len(e.todos) for e in entries)} todos,"
        f" {len(ices)} stacks on ice, {len(users)} reviewers and {len(teams)} teams"
    )


def export_notes(ctx: Context[NotesConfig], notes_dir: Path, force: bool) -> str:
    """Write `work-notes.md` and `on-ice.md` into `notes_dir`, in the skill's format."""
    repo = NotesRepository(ctx.db)
    targets = [notes_dir / markdown.WORK_NOTES_FILE, notes_dir / markdown.ON_ICE_FILE]
    existing = [str(p) for p in targets if p.exists()]
    if existing and not force:
        raise ValueError(f"{', '.join(existing)} exists; pass --force to overwrite")
    notes_dir.mkdir(parents=True, exist_ok=True)
    entries = repo.entries()
    targets[0].write_text(markdown.write_work_notes(entries, repo.sections()))
    last_asked = ctx.kv.get(LAST_ASKED_ICE)
    ices = repo.ices()
    targets[1].write_text(
        markdown.write_on_ice(ices, date.fromisoformat(last_asked) if last_asked else None)
    )
    return f"wrote {len(entries)} entries and {len(ices)} stacks on ice to {notes_dir}"


Command = Callable[[Context[NotesConfig], Graph, argparse.Namespace], "Result"]


class Result:
    """What a command did: a line to print and, with `--json`, data to print instead."""

    def __init__(self, text: str, data: object = None):
        self.text = text
        self.data = data if data is not None else {"notice": text}


def find_entry(entries: list[Entry], ref: str) -> Entry:
    """The entry `ref` names: by id or title, else by a PR URL or ticket key it's about."""
    ref = ref.strip()
    found = [e for e in entries if str(e.id) == ref or e.title == ref]
    found = found or [e for e in entries if any(link.ref == ref for link in e.links)]
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
    lines += [f"  merge condition: {text}" for text in entry.conditions]
    lines += [f"  declined {d.on}: {d.text}" for d in entry.declined]
    lines += [_todo_line(todo) for todo in entry.todos]
    return "\n".join(lines)


def _ice_text(ice: Ice) -> str:
    items = ", ".join(item.ref_id.partition("\t")[2] or item.ref_id for item in ice.items)
    note = f"  ({ice.note})" if ice.note else ""
    return f"{ice.id}  {ice.title}  iced {ice.iced_on}: {items}{note}"


def _entries(ctx: Context[NotesConfig]) -> list[Entry]:
    return NotesRepository(ctx.db).entries()


def _notice(result: str | ActionResult) -> str:
    return result if isinstance(result, str) else result.notice


def cmd_list(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    entries = [e for e in _entries(ctx) if e.open_todos or not args.open]
    text = "\n".join(_entry_line(e) for e in entries)
    return Result(text, [e.model_dump(mode="json") for e in entries])


def cmd_show(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    entry = find_entry(_entries(ctx), args.entry)
    return Result(_entry_text(entry), entry.model_dump(mode="json"))


def cmd_ices(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    ices = NotesRepository(ctx.db).ices()
    return Result("\n".join(_ice_text(i) for i in ices), [i.model_dump(mode="json") for i in ices])


def cmd_add_entry(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    if notes.entry_id(args.title) is not None:
        raise ValueError(f"there is already an entry titled {args.title!r}")
    links = [*markdown.links_in("", args.title), *(actions.link_for(graph, v) for v in args.link)]
    entry_id = notes.add_entry(
        args.title, ctx.now.date(), args.status, args.where, list(dict.fromkeys(links))
    )
    return Result(f"added entry {entry_id}", {"notice": f"added {args.title}", "id": entry_id})


def cmd_remove(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry)
    notes.delete_entry(entry.id)
    return Result(f"removed {entry.title}")


def cmd_set(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry)
    if args.field == "status":
        notes.set_status(entry.id, args.text)
    elif args.field == "where":
        notes.set_where(entry.id, args.text)
    else:
        notes.set_blocked_on(entry.id, args.text)
    notes.touch(entry.id, ctx.now.date())
    return Result(f"{entry.title}: {args.field} is now {args.text}")


def cmd_link(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry)
    link = actions.link_for(graph, args.ref)
    if not notes.link_entry(entry.id, link):
        raise ValueError(f"{entry.title} is already about {link.ref}")
    notes.touch(entry.id, ctx.now.date())
    return Result(f"{entry.title}: linked {link.ref}")


def cmd_todo_add(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry)
    text = args.text.strip()
    if not text:
        raise ValueError("the todo is empty")
    links = [*markdown.links_in(text), *(actions.link_for(graph, v) for v in args.link)]
    size: Size | None = args.size
    todo_id = notes.add_todo(entry.id, text, size, list(dict.fromkeys(links)))
    notes.touch(entry.id, ctx.now.date())
    return Result(f"added todo {todo_id}", {"notice": f"added: {text}", "id": todo_id})


def cmd_todo(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    """`todo add`, or done, undone, delete or link on the todo `args.todo` by id."""
    if args.todo_command == "add":
        return cmd_todo_add(ctx, graph, args)
    handlers = {
        "done": actions.todo_done,
        "undone": actions.todo_undone,
        "delete": actions.todo_delete,
        "link": actions.todo_link,
    }
    fields = {"todo": str(args.todo)}
    if args.todo_command == "link":
        fields["input"] = args.ref
    return Result(_notice(handlers[args.todo_command](ctx, graph, fields)))


def cmd_condition(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry)
    text = args.text.strip()
    if args.condition_command == "add":
        if not notes.add_condition(entry.id, text):
            raise ValueError(f"{entry.title} already has that merge condition")
    elif not notes.remove_condition(entry.id, text):
        raise ValueError(f"{entry.title} has no merge condition {text!r}")
    notes.touch(entry.id, ctx.now.date())
    return Result(f"{entry.title}: {args.condition_command} merge condition")


def cmd_decline(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    notes = NotesRepository(ctx.db)
    entry = find_entry(notes.entries(), args.entry)
    notes.add_declined(entry.id, ctx.now.date(), args.text.strip())
    return Result(f"{entry.title}: declined for today")


def cmd_ice(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    repo, branch = args.branch
    fields = {"repo": str(Path(repo).expanduser()), "branch": branch, "note": args.note or ""}
    return Result(_notice(actions.ice(ctx, graph, fields)))


def cmd_thaw(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    return Result(_notice(actions.thaw(ctx, graph, {"ice": str(args.ice)})))


def cmd_import(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    return Result(import_notes(ctx, Path(args.dir).expanduser(), args.replace))


def cmd_export(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> Result:
    return Result(export_notes(ctx, Path(args.dir).expanduser(), args.force))


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
        "decline": cmd_decline,
        "ice": cmd_ice,
        "thaw": cmd_thaw,
        "import": cmd_import,
        "export": cmd_export,
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
    decline = add("decline", "record that something offered for an entry was turned down today")
    decline.add_argument("entry")
    decline.add_argument("text")
    ice = add("ice", "put a branch, with its git-stack stack, on ice (needs a refresh first)")
    ice.add_argument("--branch", nargs=2, metavar=("REPO", "BRANCH"), required=True)
    ice.add_argument("--note")
    add("thaw", "take what's on ice off it").add_argument("ice", type=int)
    import_parser = add(
        "import", "read work-notes.md, on-ice.md and reviewer-map.md from a directory"
    )
    import_parser.add_argument("dir", type=Path)
    import_parser.add_argument(
        "--replace", action="store_true", help="replace the notes already imported"
    )
    export_parser = add("export", "write work-notes.md and on-ice.md into a directory")
    export_parser.add_argument("dir", type=Path)
    export_parser.add_argument("--force", action="store_true", help="overwrite existing files")
    handlers = _commands()

    def run(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> int:
        try:
            result = handlers[args.notes_command](ctx, graph, args)
        except (ValueError, ActionError) as e:
            print(f"mfd notes {args.notes_command}: {e}", file=sys.stderr)
            return 1
        print(json.dumps(result.data, indent=2) if args.json else result.text)
        return 0

    return run
