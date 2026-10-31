"""`mfd notes`: import the skill's markdown notes, and export them back."""

import argparse
import sys
from datetime import date
from pathlib import Path

from ... import notes as skill_notes
from ...graph import Graph
from ...plugin import CliHandler, Context
from ...repositories import PingRepository, ReviewerRepository
from . import markdown
from .config import NotesConfig
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
    PingRepository(ctx.db).replace_imported(skill_notes.parse_nudges(work_notes))
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


def add_commands(parser: argparse.ArgumentParser) -> CliHandler:
    commands = parser.add_subparsers(dest="notes_command", required=True)
    import_parser = commands.add_parser(
        "import", help="read work-notes.md, on-ice.md and reviewer-map.md from a directory"
    )
    import_parser.add_argument("dir", type=Path)
    import_parser.add_argument(
        "--replace", action="store_true", help="replace the notes already imported"
    )
    export_parser = commands.add_parser(
        "export", help="write work-notes.md and on-ice.md into a directory"
    )
    export_parser.add_argument("dir", type=Path)
    export_parser.add_argument("--force", action="store_true", help="overwrite existing files")

    def run(ctx: Context[NotesConfig], graph: Graph, args: argparse.Namespace) -> int:
        notes_dir = Path(args.dir).expanduser()
        try:
            if args.notes_command == "import":
                print(import_notes(ctx, notes_dir, args.replace))
            else:
                print(export_notes(ctx, notes_dir, args.force))
        except ValueError as e:
            print(f"mfd notes {args.notes_command}: {e}", file=sys.stderr)
            return 1
        return 0

    return run
