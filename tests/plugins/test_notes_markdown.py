"""Reading the skill's markdown notes into the notes plugin's models, and writing them back;
and `mfd notes import` / `export`."""

from datetime import date
from pathlib import Path

import pytest
from skill_notes import ON_ICE, REVIEWER_MAP, WORK_NOTES

from move_forward import cli
from move_forward.db import Database
from move_forward.plugins.notes import markdown
from move_forward.plugins.notes.models import Link
from move_forward.plugins.notes.repository import MIGRATIONS, NotesRepository

TODAY = date(2026, 10, 31)
PR_12 = "https://github.com/acme/widgets/pull/12"
PR_13 = "https://github.com/acme/widgets/pull/13"


def test_an_entry_is_read_field_by_field() -> None:
    entries, sections = markdown.read_work_notes(WORK_NOTES)
    assert [e.title for e in entries] == ["APP-1 Fix the widget", "Thing without ticket (inferred)"]
    entry = entries[0]
    assert (entry.status, entry.blocked_on, entry.updated_on) == (
        "in review",
        "reviews",
        date(2026, 10, 29),
    )
    assert entry.where == f"engineering {PR_12}, {PR_13}"
    assert [(t.text, t.size, t.done_on) for t in entry.todos] == [
        ("reply to the thread", "quick", None),
        ("get it merged", "big", None),
        ("no size here", None, None),
    ]
    assert entry.conditions == [f"don't merge until {PR_13} is merged"]
    assert [(d.on, d.text) for d in entry.declined] == [
        (date(2026, 10, 29), "don't nudge today"),
        (date(2026, 10, 28), "skip the review"),
    ]
    assert entry.links == [
        Link(kind="pr", ref=PR_12),
        Link(kind="pr", ref=PR_13),
    ]  # APP-1 in its title isn't a link: the graph finds it
    assert [s.heading for s in sections] == [None, markdown.REVIEWS_SECTION]
    assert sections[0].body == "# Work notes\n\nIntro text."


def test_done_todos_and_unknown_lines_are_kept() -> None:
    text = (
        "# Work notes\n\n## APP-2 Thing\n- Status: x\n- Odd field: kept\n  - its child\n"
        "- Done 2026-10-30: [quick] replied\n- Status: second status, kept\n- Updated: soon\n"
    )
    [entry], _ = markdown.read_work_notes(text)
    assert [(t.text, t.size, t.done_on) for t in entry.todos] == [
        ("replied", "quick", date(2026, 10, 30))
    ]
    assert entry.status == "x" and entry.updated_on is None
    assert entry.lines == [
        "- Odd field: kept",
        "  - its child",
        "- Status: second status, kept",
        "- Updated: soon",
    ]


def test_work_notes_written_back_read_the_same() -> None:
    entries, sections = markdown.read_work_notes(WORK_NOTES)
    again = markdown.read_work_notes(markdown.write_work_notes(entries, sections))
    assert again == (entries, sections)


def test_stacks_on_ice_round_trip() -> None:
    ices, last_asked = markdown.read_on_ice(ON_ICE, TODAY)
    assert last_asked == date(2026, 10, 30)
    assert [(i.title, i.iced_on, i.note) for i in ices] == [
        ("me/bottom", date(2026, 10, 28), "parked"),
        ("me/other", date(2026, 10, 29), None),
    ]
    widgets = str(Path("~/src/widgets").expanduser())
    assert [(it.ref_id, it.sha) for it in ices[0].items] == [
        (f"{widgets}\tme/bottom", "abc123"),
        (f"{widgets}\tme/top", "def456"),
    ]
    assert markdown.read_on_ice(markdown.write_on_ice(ices, last_asked), TODAY) == (
        ices,
        last_asked,
    )
    assert "## me/bottom (~/src/widgets)" in markdown.write_on_ice(ices, last_asked)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'email = "me@x"\ndb_path = "{(tmp_path / "dashboard.db").as_posix()}"\n')
    return path


def _notes_dir(tmp_path: Path) -> Path:
    notes_dir = tmp_path / "skill"
    notes_dir.mkdir()
    (notes_dir / markdown.WORK_NOTES_FILE).write_text(WORK_NOTES)
    (notes_dir / markdown.ON_ICE_FILE).write_text(ON_ICE)
    (notes_dir / "reviewer-map.md").write_text(REVIEWER_MAP)
    return notes_dir


def test_mfd_notes_import_then_export(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes_dir = _notes_dir(tmp_path)
    assert cli.main(["-c", str(config), "notes", "import", str(notes_dir)]) == 0
    assert "imported 2 entries, 3 todos and 2 stacks on ice" in capsys.readouterr().out
    db = Database(tmp_path / "dashboard.db")
    assert [e.title for e in NotesRepository(db).entries()] == [
        "APP-1 Fix the widget",
        "Thing without ticket (inferred)",
    ]

    assert cli.main(["-c", str(config), "notes", "import", str(notes_dir)]) == 1
    assert "--replace" in capsys.readouterr().err
    assert cli.main(["-c", str(config), "notes", "import", str(notes_dir), "--replace"]) == 0

    out = tmp_path / "out"
    assert cli.main(["-c", str(config), "notes", "export", str(out)]) == 0
    exported = (out / markdown.WORK_NOTES_FILE).read_text()
    assert markdown.read_work_notes(exported) == markdown.read_work_notes(WORK_NOTES)
    assert markdown.read_on_ice((out / markdown.ON_ICE_FILE).read_text(), TODAY) == (
        markdown.read_on_ice(ON_ICE, TODAY)
    )
    assert cli.main(["-c", str(config), "notes", "export", str(out)]) == 1
    assert cli.main(["-c", str(config), "notes", "export", str(out), "--force"]) == 0


def test_a_branch_in_two_stacks_on_ice_imports_with_the_first(tmp_path: Path) -> None:
    db = Database(tmp_path / "d.db")
    db.migrate("notes", MIGRATIONS)
    text = ON_ICE + "\n## me/again (~/src/widgets)\n- Branches:\n  - me/top 999\n  - me/new 1\n"
    ices, _ = markdown.read_on_ice(text, TODAY)
    NotesRepository(db).replace_all([], ices, [])
    stored = {
        i.title: [item.ref_id.partition("\t")[2] for item in i.items]
        for i in NotesRepository(db).ices()
    }
    assert stored["me/bottom"] == ["me/bottom", "me/top"] and stored["me/again"] == ["me/new"]
