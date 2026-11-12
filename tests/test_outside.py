"""The guard keeping tests to themselves (conftest's `outside`): no program but git, no network
but this machine, and not the real secret store."""

import contextlib
import os
import socket
import subprocess
import sys

import pytest
from conftest import Outside, OutsideRefused

from move_forward import keychain

ELSEWHERE = ("192.0.2.1", 80)
"""An address in TEST-NET-1, which no machine has: refused before it's tried."""


def test_a_program_but_git_is_refused(outside: Outside) -> None:
    popen = subprocess.Popen  # bound before, as `supervise`'s default argument is
    for start in (
        lambda: subprocess.run(["gh", "api", "user"], check=False),
        lambda: subprocess.check_output("gh auth status"),
        lambda: subprocess.run("git --version", shell=True, check=False),  # sh could run anything
        lambda: popen(["secret-tool", "lookup", "service", "s"]),
        lambda: subprocess.run([sys.executable, "-c", "pass"], check=False),  # unless allowed
    ):
        with pytest.raises(OutsideRefused):
            start()
    assert outside.take() == [
        "the program `gh api user`",
        "the program `gh auth status`",
        "the program `git --version`",
        "the program `secret-tool lookup service s`",
        f"the program `{sys.executable} -c pass`",
    ]


def test_os_and_pty_start_no_program_either(outside: Outside) -> None:
    with pytest.raises(OutsideRefused):
        os.system("gh api user")  # pyright: ignore[reportDeprecated]
    with pytest.raises(OutsideRefused):
        os.execv("/usr/bin/gh", ["gh"])
    with pytest.raises(OutsideRefused):
        os.spawnv(os.P_WAIT, "/usr/bin/gh", ["gh"])  # pyright: ignore[reportDeprecated]
    assert outside.take() == [
        "os.system('gh api user')",
        "the program `/usr/bin/gh`, through os.execv",
        "the program `/usr/bin/gh`, through os.spawnv",
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="posix_spawn and pty are POSIX's")
def test_posix_spawn_and_a_terminal_are_refused(outside: Outside) -> None:
    import pty

    with pytest.raises(OutsideRefused):
        os.posix_spawn("/usr/bin/gh", ["gh"], {})
    with pytest.raises(OutsideRefused):
        pty.spawn(["gh"])
    with pytest.raises(OutsideRefused):
        os.forkpty()
    assert outside.take() == [
        "the program `/usr/bin/gh`, through os.posix_spawn",
        "pty.spawn(['gh'])",
        "os.forkpty()",
    ]


def test_git_runs_but_fetches_from_no_network(outside: Outside) -> None:
    assert subprocess.run(["git", "--version"], capture_output=True, check=True).stdout
    for program in ("git", "GIT.EXE", "C:/Program Files/Git/cmd/git.exe"):
        assert outside.may_run(program), program
    assert not outside.may_run("/usr/bin/gh") and not outside.may_run("git-stack")
    fetch = ["git", "ls-remote", "https://example.invalid/r.git"]
    fetched = subprocess.run(fetch, capture_output=True, encoding="utf-8", check=False)
    assert fetched.returncode != 0 and "not allowed" in fetched.stderr


def test_a_program_a_test_allows_runs(outside: Outside) -> None:
    outside.allow("MFD-No-Such-Program.exe")  # as `may_run` compares it
    with pytest.raises(FileNotFoundError):  # tried, and not there
        subprocess.run(["mfd-no-such-program"], check=False)
    outside.allow(sys.executable)
    python = [sys.executable, "-c", "print('hi')"]
    assert subprocess.run(python, capture_output=True, check=True, text=True).stdout == "hi\n"
    assert outside.refused == []


def test_a_connection_off_this_machine_is_refused(outside: Outside) -> None:
    with socket.socket() as s, pytest.raises(OutsideRefused):
        s.connect(ELSEWHERE)
    with socket.socket() as s, pytest.raises(OutsideRefused):
        s.connect_ex(ELSEWHERE)
    with socket.socket(type=socket.SOCK_DGRAM) as s, pytest.raises(OutsideRefused):
        s.sendto(b"x", ELSEWHERE)
    with pytest.raises(OutsideRefused):
        socket.create_connection(("example.com", 443))  # refused at the name's lookup
    assert outside.take() == [f"the network, at {ELSEWHERE}"] * 3 + [
        "the network, through socket.getaddrinfo('example.com')"
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows' sockets have no sendmsg")
def test_a_message_off_this_machine_is_refused(outside: Outside) -> None:
    with socket.socket(type=socket.SOCK_DGRAM) as s, pytest.raises(OutsideRefused):
        s.sendmsg([b"x"], [], 0, ELSEWHERE)
    assert outside.take() == [f"the network, at {ELSEWHERE}"]


def test_a_name_or_an_address_elsewhere_isnt_looked_up(outside: Outside) -> None:
    for look_up in (
        lambda: socket.gethostbyname("example.com"),
        lambda: socket.gethostbyname_ex("example.com"),
        lambda: socket.gethostbyaddr(ELSEWHERE[0]),
        lambda: socket.getnameinfo(ELSEWHERE, 0),
    ):
        with pytest.raises(OutsideRefused):
            look_up()
    assert outside.take() == [
        "the network, through socket.gethostbyname('example.com')",
        "the network, through socket.gethostbyname_ex('example.com')",
        f"the network, through socket.gethostbyaddr({ELSEWHERE[0]!r})",
        f"the network, through socket.getnameinfo({ELSEWHERE!r})",
    ]


def test_a_connection_to_this_machine_is_allowed(outside: Outside) -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        with socket.create_connection(("localhost", port), timeout=5):
            pass
    assert socket.gethostbyname("localhost")
    assert outside.refused == []


def test_windows_credential_manager_is_refused(outside: Outside) -> None:
    with pytest.raises(OutsideRefused):
        keychain._advapi32()  # pyright: ignore[reportPrivateUsage]
    assert outside.take() == ["Windows' Credential Manager"]


def test_a_refused_call_the_code_swallows_still_fails_the_test(outside: Outside) -> None:
    with contextlib.suppress(OSError):
        subprocess.run(["gh", "api", "user"], check=False)
    with pytest.raises(pytest.fail.Exception, match="the program `gh api user`"):
        outside.check()
    outside.take()
    outside.check()  # nothing left to fail it
