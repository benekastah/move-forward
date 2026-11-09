"""`mfd import <dir>`: each enabled plugin reads what it knows of the skill's markdown."""

from pathlib import Path

import pytest
from skill_notes import REVIEWER_MAP, WORK_NOTES

from move_forward import cli


def _config(tmp_path: Path, sections: str) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        f'email = "me@x"\ndb_path = "{(tmp_path / "d.db").as_posix()}"\n\n{sections}',
        encoding="utf-8",
    )
    return config


def test_each_enabled_plugin_imports_what_it_knows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes_dir = tmp_path / "skill"
    notes_dir.mkdir()
    (notes_dir / "work-notes.md").write_text(WORK_NOTES, encoding="utf-8")
    (notes_dir / "reviewer-map.md").write_text(REVIEWER_MAP, encoding="utf-8")
    config = _config(tmp_path, '[github]\norg = "acme"\n\n[slack]\n')
    assert cli.main(["-c", str(config), "import", str(notes_dir)]) == 0
    said = capsys.readouterr().out.splitlines()
    assert list(dict.fromkeys(line.partition(":")[0] for line in said)) == [
        "github",
        "notes",
        "slack",
    ]


def test_one_plugin_refusing_doesnt_stop_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    notes_dir = tmp_path / "skill"
    notes_dir.mkdir()
    (notes_dir / "work-notes.md").write_text(WORK_NOTES, encoding="utf-8")
    config = _config(tmp_path, "[slack]\n")
    assert cli.main(["-c", str(config), "import", str(notes_dir)]) == 0
    capsys.readouterr()
    # The notes are there now, so notes refuses without --replace; slack imports again.
    assert cli.main(["-c", str(config), "import", str(notes_dir)]) == 1
    out = capsys.readouterr()
    assert "mfd import: notes: " in out.err and "--replace" in out.err
    assert out.out.startswith("slack: imported")
    assert cli.main(["-c", str(config), "import", str(notes_dir), "--replace"]) == 0


def test_a_directory_with_nothing_to_import_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path, "")
    assert cli.main(["-c", str(config), "import", str(tmp_path / "typo")]) == 1
    assert "isn't a directory" in capsys.readouterr().err
    (tmp_path / "empty").mkdir()
    assert cli.main(["-c", str(config), "import", str(tmp_path / "empty")]) == 1
    assert "nothing in" in capsys.readouterr().err
