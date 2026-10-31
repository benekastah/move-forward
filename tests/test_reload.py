import os
import sys
from pathlib import Path

import pytest

from move_forward_dashboard import reload


def test_changed_sees_edits_additions_and_removals(tmp_path: Path) -> None:
    a, b = tmp_path / "a.py", tmp_path / "b.css"
    a.write_text("x = 1")
    b.write_text("")
    before = reload.snapshot(reload.watched_files(tmp_path))
    assert reload.changed(before, tmp_path) == []
    (tmp_path / "notes.txt").write_text("not watched")
    assert reload.changed(before, tmp_path) == []
    os.utime(a, (0, 0))
    b.unlink()
    (tmp_path / "c.py").write_text("")
    assert reload.changed(before, tmp_path) == [a, b, tmp_path / "c.py"]


def test_compiles_rejects_a_syntax_error(tmp_path: Path) -> None:
    good, bad, gone = tmp_path / "good.py", tmp_path / "bad.py", tmp_path / "gone.py"
    good.write_text("x = 1\n")
    bad.write_text("def (:\n")
    assert reload.compiles([good, gone, tmp_path / "style.css"])
    assert not reload.compiles([good, bad])


def test_restart_command_drops_one_shot_flags() -> None:
    argv = ["/x/bin/mfd", "-c", "cfg.toml", "serve", "--open", "--reload", "--port", "1"]
    assert reload.restart_command(argv) == [
        sys.executable,
        "-m",
        "move_forward_dashboard.cli",
        "-c",
        "cfg.toml",
        "serve",
        "--reload",
        "--port",
        "1",
    ]


class Restarted(Exception):
    """Raised by the fake restart; the real one execs and never returns."""


def test_watch_restarts_once_a_change_compiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sleeps are the clock: a broken edit on the 2nd poll is waited out, a good one restarts."""
    source = tmp_path / "a.py"
    source.write_text("x = 1\n")
    polls = 0

    def sleep(_: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 2:
            source.write_text("def (:\n")
            os.utime(source, (1, 1))
        elif polls == 6:
            source.write_text("x = 2\n")
            os.utime(source, (2, 2))
        elif polls > 20:
            pytest.fail("never restarted")

    def restart() -> None:
        raise Restarted

    monkeypatch.setattr(reload.time, "sleep", sleep)
    with pytest.raises(Restarted):
        reload.watch(restart, tmp_path)
    assert polls > 6
