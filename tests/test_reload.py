import os
import sys
from pathlib import Path

import pytest

from move_forward import reload


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
        "move_forward.cli",
        "-c",
        "cfg.toml",
        "serve",
        "--reload",
        "--port",
        "1",
    ]


class Restarted(Exception):
    """Raised by the fake restart, to end the watch."""


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
        reload.watch(restart, tmp_path, check=lambda: True)
    assert polls > 6


def test_a_change_that_fails_the_check_is_waited_out_and_the_config_is_watched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source change fails the check (say, a bad import); then the config changes, the check
    passes, and the server restarts."""
    source, config = tmp_path / "pkg" / "a.py", tmp_path / "config.toml"
    source.parent.mkdir()
    source.write_text("x = 1\n")
    config.write_text("")
    polls = 0
    checks: list[bool] = []

    def sleep(_: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 2:
            os.utime(source, (1, 1))
        elif polls == 6:
            os.utime(config, (2, 2))
        elif polls > 20:
            pytest.fail("never restarted")

    def check() -> bool:
        checks.append(polls >= 6)
        return checks[-1]

    def restart() -> None:
        raise Restarted

    monkeypatch.setattr(reload.time, "sleep", sleep)
    with pytest.raises(Restarted):
        reload.watch(restart, tmp_path / "pkg", extra=[config], check=check)
    assert checks == [False, True]


def test_check_command_keeps_the_global_options() -> None:
    argv = ["/x/bin/mfd", "-c", "cfg.toml", "-v", "serve", "--reload", "--port", "1"]
    assert reload.check_command(argv) == [
        sys.executable,
        "-m",
        "move_forward.cli",
        "-c",
        "cfg.toml",
        "-v",
        "check",
    ]


def test_passes_check_runs_mfd_check_in_a_fresh_process(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('email = "me@x"\n')
    assert reload.passes_check(["mfd", "-c", str(config), "serve"])
    config.write_text('email = "me@x"\nrepos = []\n')
    assert not reload.passes_check(["mfd", "-c", str(config), "serve"])


def test_without_reload_only_the_config_is_watched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("")
    polls = 0

    def sleep(_: float) -> None:
        nonlocal polls
        polls += 1
        if polls == 2:
            os.utime(config, (2, 2))
        elif polls > 10:
            pytest.fail("never restarted")

    def restart() -> None:
        raise Restarted

    monkeypatch.setattr(reload.time, "sleep", sleep)
    assert reload.watched_files(None) == []
    with pytest.raises(Restarted):
        reload.watch(restart, None, extra=[config], check=lambda: True)
