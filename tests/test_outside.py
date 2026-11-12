"""The guard keeping tests to themselves (conftest's `outside`): no program but git, git stack
and Python, no network but this machine, and not the real secret store."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import Outside, OutsideRefused

from move_forward import keychain

ELSEWHERE = ("192.0.2.1", 80)
"""An address in TEST-NET-1, which no machine has: refused before it's tried."""


def test_a_program_but_git_or_python_is_refused(outside: Outside) -> None:
    popen = subprocess.Popen  # bound before, as `supervise`'s default argument is
    for start in (
        lambda: subprocess.run(["gh", "api", "user"], check=False),
        lambda: subprocess.check_output("gh auth status"),
        lambda: subprocess.run("git --version", shell=True, check=False),  # sh could run anything
        lambda: popen(["secret-tool", "lookup", "service", "s"]),
    ):
        with pytest.raises(OutsideRefused):
            start()
    assert outside.take() == [
        "the program `gh api user`",
        "the program `gh auth status`",
        "the program `git --version`",
        "the program `secret-tool lookup service s`",
    ]


def test_git_and_python_run(outside: Outside) -> None:
    assert subprocess.run(["git", "--version"], capture_output=True, check=True).stdout
    python = [sys.executable, "-c", "print('hi')"]
    assert subprocess.run(python, capture_output=True, check=True, text=True).stdout == "hi\n"
    for program in ("git", "GIT.EXE", "C:/Program Files/Git/cmd/git.exe", "/usr/bin/git-stack"):
        assert outside.may_run(program), program
    assert not outside.may_run("/usr/bin/gh")


def test_a_program_a_test_allows_runs(outside: Outside) -> None:
    outside.allow("mfd-no-such-program")
    with pytest.raises(FileNotFoundError):  # tried, and not there
        subprocess.run(["mfd-no-such-program"], check=False)
    assert outside.refused == []


def test_a_connection_off_this_machine_is_refused(outside: Outside) -> None:
    with socket.socket() as s, pytest.raises(OutsideRefused):
        s.connect(ELSEWHERE)
    with socket.socket() as s, pytest.raises(OutsideRefused):
        s.connect_ex(ELSEWHERE)
    with pytest.raises(OutsideRefused):
        socket.create_connection(("example.com", 443))  # refused at the name's lookup
    assert outside.take() == [
        f"the network, at {ELSEWHERE}",
        f"the network, at {ELSEWHERE}",
        "the network, looking up 'example.com'",
    ]


def test_a_connection_to_this_machine_is_allowed(outside: Outside) -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        with socket.create_connection(("localhost", port), timeout=5):
            pass
    assert outside.refused == []


def test_windows_credential_manager_is_refused(outside: Outside) -> None:
    with pytest.raises(OutsideRefused):
        keychain._advapi32()  # pyright: ignore[reportPrivateUsage]
    assert outside.take() == ["Windows' Credential Manager"]


def test_a_refused_call_the_code_swallows_still_fails_the_test(tmp_path: Path) -> None:
    """Run in a pytest of its own, with this conftest: the call is caught, the test passes,
    and its teardown fails it, naming the call."""
    shutil.copy(Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py")
    (tmp_path / "test_swallowing.py").write_text(
        "import contextlib, subprocess\n"
        "\n"
        "def test_swallowing() -> None:\n"
        "    with contextlib.suppress(OSError):\n"
        "        subprocess.run(['gh', 'api', 'user'])\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "GIT_"))}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(tmp_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed, 1 error" in result.stdout
    assert "the test reached outside itself" in result.stdout
    assert "the program `gh api user`" in result.stdout
