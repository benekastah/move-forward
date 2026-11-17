"""dev.py: which Python it needs; and dev_project.py, what move-forward adds to it: `install`
writing the config."""

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent
SPEC = importlib.util.spec_from_file_location("dev", ROOT / "dev.py")
assert SPEC is not None and SPEC.loader is not None
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)


def test_the_python_needed_is_read_from_requires_python() -> None:
    assert dev.required_python('[project]\nrequires-python = ">=3.14"\n') == (3, 14)
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert dev.required_python(pyproject) <= sys.version_info[:2]  # the tests' Python passes


def test_only_an_older_python_is_too_old() -> None:
    assert dev.too_old((3, 13), (3, 14)) is not None
    assert dev.too_old((3, 14), (3, 14)) is None
    assert dev.too_old((3, 15), (3, 14)) is None


def _clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, venv_made_with: str, uv: str | None
) -> list[tuple[str, ...]]:
    """A clone needing 3.14 whose .venv an older dev.py install made with `venv_made_with`,
    with dev.py running under 3.12 and `uv` on PATH (or not): the commands dev.py runs."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.14"\n', encoding="utf-8"
    )
    venv = tmp_path / ".venv"
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "VENV", venv)
    monkeypatch.setattr(dev, "BIN", venv / "bin")
    monkeypatch.setattr(dev, "STAMP", venv / "installed")
    python = Path(dev.tool("python"))
    python.parent.mkdir(parents=True)
    python.touch()
    (venv / "pyvenv.cfg").write_text(f"version = {venv_made_with}\n", encoding="utf-8")

    def which(_command: str) -> str | None:
        return uv

    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> None:
        calls.append(args)

    monkeypatch.setattr(dev.shutil, "which", which)
    monkeypatch.setattr(dev, "run", run)
    monkeypatch.setattr(dev, "_running", lambda: (3, 12))
    return calls


def test_without_uv_an_older_python_is_told_which_to_make_venv_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _clone(tmp_path, monkeypatch, "3.12.3", uv=None)

    with pytest.raises(SystemExit) as stopped:
        dev.venv()

    assert "this is 3.12" in str(stopped.value.code)
    assert "python3.14" in str(stopped.value.code)
    assert "py -3.14" in str(stopped.value.code)
    assert (tmp_path / ".venv" / "pyvenv.cfg").exists()  # not removed for one it can't make
    assert calls == []


def test_without_uv_an_older_python_installs_into_a_new_enough_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As the hooks do, whatever `python3` is."""
    calls = _clone(tmp_path, monkeypatch, "3.14.0", uv=None)

    dev.venv()

    assert calls
    assert all(call[0] == dev.tool("python") for call in calls)  # pip in .venv; no new venv


def test_with_uv_any_python_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """uv picks (or downloads) a Python that's new enough itself."""
    calls = _clone(tmp_path, monkeypatch, "3.12.3", uv="/usr/bin/uv")
    monkeypatch.delenv("CI", raising=False)

    dev.venv()

    assert calls == [("uv", "sync", "--quiet")]


def test_with_uv_ci_installs_only_what_uv_lock_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In CI, an out-of-date uv.lock fails rather than being updated."""
    calls = _clone(tmp_path, monkeypatch, "3.14.0", uv="/usr/bin/uv")
    monkeypatch.setenv("CI", "true")

    dev.venv()

    assert calls == [("uv", "sync", "--quiet", "--locked")]


@pytest.mark.parametrize(
    ("cfg", "made_with"),
    [
        ("home = /usr/bin\nversion = 3.12.3\nexecutable = /usr/bin/python3.12\n", (3, 12)),
        ("home = /usr/bin\nimplementation = CPython\nversion_info = 3.14.0\n", (3, 14)),
        ("home = /usr/bin\n", None),
    ],
)
def test_the_python_a_venv_was_made_with_is_read_from_pyvenv_cfg(
    cfg: str, made_with: tuple[int, int] | None
) -> None:
    assert dev.venv_python(cfg) == made_with


def test_install_links_mfd_and_runs_dev_projects_after_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`install` puts `mfd` on PATH and runs this repo's dev_project.py `after_install`, which
    writes the config (stubbed here; its own tests are below)."""
    after: list[bool] = []
    monkeypatch.setattr(dev_project(monkeypatch), "after_install", lambda: after.append(True))
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> None:
        calls.append(args)

    monkeypatch.setattr(dev, "venv", lambda: None)
    monkeypatch.setattr(dev, "run", run)
    monkeypatch.setattr(dev, "_bin_dir", lambda: tmp_path)

    # The clone running the tests may be a scratch one in a temporary directory, which install
    # refuses outright; this install goes to tmp_path anyway.
    def not_temporary(names: list[str]) -> None:
        return None

    monkeypatch.setattr(dev, "_temporary", not_temporary)
    # On PATH already, so install leaves the user's PATH alone (on Windows, the registry).
    monkeypatch.setenv("PATH", str(tmp_path))

    dev.install([])

    assert after == [True]
    assert (tmp_path / ("mfd.cmd" if dev.WINDOWS else "mfd")).exists(follow_symlinks=False)
    assert ("git", "config", "core.hooksPath", "git_hooks") in calls


def dev_project(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """dev_project.py, imported as dev.py imports it, calling back into this test's `dev`."""
    monkeypatch.setattr(sys, "path", [str(ROOT), *sys.path])
    project = importlib.import_module("dev_project")
    monkeypatch.setattr(project, "dev", dev)
    return project


def _after_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: bool, terminal: bool
) -> list[tuple[str, ...]]:
    """Run `after_install` with the config there (or not) and stdin a terminal (or not): the
    commands it runs."""
    project = dev_project(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    if config:
        (tmp_path / "move-forward").mkdir()
        (tmp_path / "move-forward" / "config.toml").touch()
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> None:
        calls.append(args)

    monkeypatch.setattr(dev, "run", run)
    monkeypatch.setattr(project.sys.stdin, "isatty", lambda: terminal)
    project.after_install()
    return calls


def test_install_asks_for_a_config_in_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _after_install(tmp_path, monkeypatch, config=False, terminal=True)

    assert calls == [(dev.tool("mfd"), "init")]


@pytest.mark.parametrize(("config", "terminal"), [(True, True), (False, False)])
def test_install_leaves_a_config_alone_and_asks_nothing_outside_a_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config: bool,
    terminal: bool,
) -> None:
    calls = _after_install(tmp_path, monkeypatch, config=config, terminal=terminal)

    assert calls == []
    assert str(tmp_path / "move-forward" / "config.toml") in capsys.readouterr().out
