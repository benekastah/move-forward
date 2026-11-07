"""dev.py's pre-push hook: which pushes it checks."""

import importlib.util
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
