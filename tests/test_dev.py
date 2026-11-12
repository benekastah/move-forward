"""dev.py: which Python it needs, and which pushes its pre-push hook checks."""

import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("dev", Path(__file__).parent.parent / "dev.py")
assert SPEC is not None and SPEC.loader is not None
dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dev)

SHA = "a" * 40
GONE = "0" * 40


@pytest.mark.parametrize(
    ("refs", "checked"),
    [
        (f"refs/heads/main {SHA} refs/heads/main {GONE}\n", True),
        (f"refs/heads/x {SHA} refs/heads/main {GONE}\n", True),
        (f"refs/heads/x {SHA} refs/heads/x {GONE}\n", False),
        (
            f"refs/heads/x {SHA} refs/heads/x {GONE}\nrefs/heads/main {SHA} refs/heads/main {SHA}\n",
            True,
        ),
        (f"(delete) {GONE} refs/heads/main {SHA}\n", False),
        (f"(delete) {GONE} refs/heads/x {SHA}\n", False),
        ("", False),
    ],
)
def test_only_a_push_bringing_commits_to_main_is_checked(refs: str, checked: bool) -> None:
    assert dev.pushes_to_main(refs) is checked


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

    dev.venv()

    assert calls == [("uv", "sync", "--quiet")]


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
