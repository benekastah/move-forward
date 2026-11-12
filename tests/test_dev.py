"""dev.py: which Python it needs, and which pushes its pre-push hook checks."""

import importlib.util
import io
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


def _not_installed(_command: str) -> None:
    return None


def _nothing_staged(*_args: str, stdin: bytes | None = None) -> bytes:
    return b""


def test_without_uv_an_older_python_is_told_which_to_make_venv_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nrequires-python = ">=3.14"\n', encoding="utf-8"
    )
    old = tmp_path / ".venv"
    old.mkdir()
    (old / "pyvenv.cfg").write_text("version = 3.12.3\n", encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "VENV", old)
    monkeypatch.setattr(dev, "STAMP", old / "installed")
    monkeypatch.setattr(dev.shutil, "which", _not_installed)
    monkeypatch.setattr(dev, "_running", lambda: (3, 12))

    with pytest.raises(SystemExit) as stopped:
        dev.venv()

    assert "this is 3.12" in str(stopped.value.code)
    assert "python3.14" in str(stopped.value.code)
    assert "py -3.14" in str(stopped.value.code)
    assert (old / "pyvenv.cfg").exists()  # not removed for a .venv it can't make


def test_the_hooks_run_under_an_older_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """They run `python3`, whatever it is; with nothing to check, they pass."""
    monkeypatch.setattr(dev, "_running", lambda: (3, 12))
    monkeypatch.setattr(dev, "_git", _nothing_staged)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # nothing pushed to main

    dev.main(["pre-commit"])
    dev.main(["pre-push"])


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
