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


def test_an_older_python_is_told_which_to_run_dev_py_with() -> None:
    problem = dev.too_old((3, 12), (3, 14))
    assert problem is not None
    assert "this is 3.12" in problem
    assert "python3.14" in problem
    assert "py -3.14" in problem
    assert dev.too_old((3, 14), (3, 14)) is None
    assert dev.too_old((3, 15), (3, 14)) is None


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
