"""`mfd serve`'s parent process: replacing, restarting and stopping the server."""

import errno
import os
import signal
from collections.abc import Callable
from pathlib import Path

import pytest

from move_forward import cli, supervisor


class FakeServer:
    """A server process: running until `code` is set, by the test or by `terminate`."""

    def __init__(self, command: list[str], env: dict[str, str]) -> None:
        self.command, self.env = command, env
        self.code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        assert self.code is not None
        return self.code

    def terminate(self) -> None:
        self.terminated, self.code = True, -15

    def kill(self) -> None:
        self.code = -9


def test_serve_replaces_its_server_on_a_change_and_ends_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    servers: list[FakeServer] = []

    def popen(command: list[str], env: dict[str, str]) -> FakeServer:
        servers.append(FakeServer(command, env))
        return servers[-1]

    def watch(restart: Callable[[], None], *args: object, alive: Callable[[], bool]) -> None:
        assert alive()
        assert args[1] == [
            tmp_path / "c.toml",
            supervisor.restart_request_path(tmp_path / "c.toml"),
        ]
        restart()  # the config changed and passes the check
        servers[-1].code = 0  # then it exits cleanly

    monkeypatch.setattr(supervisor, "watch", watch)
    argv = ["mfd", "serve", "--open"]
    assert supervisor.supervise(argv, tmp_path / "c.toml", code=False, popen=popen) == 0
    first, second = servers
    assert first.terminated and not second.terminated
    assert first.command[-2:] == ["serve", "--open"] and second.command[-1] == "serve"
    assert first.env[supervisor.SUPERVISED_ENV] == second.env[supervisor.SUPERVISED_ENV] == "1"


def test_restart_command_sends_a_portable_request(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('email = "me@example.com"\n')
    assert cli.main(["-c", str(config), "restart"]) == 0
    request = supervisor.restart_request_path(config)
    first = request.read_text()
    assert first.strip().isdigit()
    assert cli.main(["-c", str(config), "restart"]) == 0
    assert request.read_text() != first


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="Windows uses mfd restart")
def test_usr1_to_the_supervisor_requests_the_same_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    handlers: list[Callable[..., object]] = []

    def register(signum: int, handler: Callable[..., object]) -> None:
        if signum == signal.SIGUSR1:
            handlers.append(handler)

    monkeypatch.setattr(supervisor.signal, "signal", register)
    server = FakeServer([], {})

    def popen(command: list[str], env: dict[str, str]) -> FakeServer:
        return server

    def watch(restart: Callable[[], None], *args: object, alive: Callable[[], bool]) -> None:
        assert alive() and handlers
        handlers[0](signal.SIGUSR1, None)
        assert supervisor.restart_request_path(config).exists()
        server.code = 0

    monkeypatch.setattr(supervisor, "watch", watch)
    assert supervisor.supervise(["mfd", "serve"], config, code=False, popen=popen) == 0


def test_a_failed_server_is_started_again_until_it_keeps_failing_as_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    servers: list[FakeServer] = []
    now = 0.0

    def popen(command: list[str], env: dict[str, str]) -> FakeServer:
        servers.append(FakeServer(command, env))
        return servers[-1]

    def watch(restart: Callable[[], None], *args: object, alive: Callable[[], bool]) -> None:
        nonlocal now
        now += 100 if len(servers) == 1 else 1  # the first runs a while, the rest fail at once
        servers[-1].code = 1

    monkeypatch.setattr(supervisor, "watch", watch)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now)

    def sleep(_: float) -> None:
        pass

    monkeypatch.setattr(supervisor.time, "sleep", sleep)
    assert supervisor.supervise(["mfd", "serve"], tmp_path / "c.toml", code=False, popen=popen) == 1
    assert len(servers) == 1 + supervisor.MAX_QUICK_FAILURES


def test_a_busy_port_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'email = "me@example.com"\ndb_path = "{(tmp_path / "dashboard.db").as_posix()}"\n'
    )
    monkeypatch.setattr(supervisor, "supervised", lambda: True)
    monkeypatch.setattr(supervisor, "serve_supervised", lambda: None)

    def busy(*_: object, **__: object) -> None:
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(cli, "serve", busy)
    assert (
        cli.main(["-c", str(config), "serve", "--port", "9876"]) == supervisor.PORT_IN_USE_EXIT_CODE
    )
    assert "port 9876 is already in use" in capsys.readouterr().err

    servers: list[FakeServer] = []

    def popen(command: list[str], env: dict[str, str]) -> FakeServer:
        servers.append(FakeServer(command, env))
        return servers[-1]

    def watch(restart: Callable[[], None], *args: object, alive: Callable[[], bool]) -> None:
        servers[-1].code = supervisor.PORT_IN_USE_EXIT_CODE

    monkeypatch.setattr(supervisor, "watch", watch)
    assert (
        supervisor.supervise(["mfd", "serve"], config, code=False, popen=popen)
        == supervisor.PORT_IN_USE_EXIT_CODE
    )
    assert len(servers) == 1


def test_ctrl_c_stops_the_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    servers: list[FakeServer] = []

    def popen(command: list[str], env: dict[str, str]) -> FakeServer:
        servers.append(FakeServer(command, env))
        return servers[-1]

    def watch(restart: Callable[[], None], *args: object, alive: Callable[[], bool]) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(supervisor, "watch", watch)
    assert supervisor.supervise(["mfd", "serve"], tmp_path / "c.toml", code=False, popen=popen) == 0
    assert [s.terminated for s in servers] == [True]


def test_the_server_leaves_ctrl_c_to_the_parent_but_not_for_its_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(supervisor.SUPERVISED_ENV, "1")
    before = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        supervisor.serve_supervised()
        handler = signal.getsignal(signal.SIGINT)
        # Ignored by a handler, which commands it starts don't inherit, unlike SIG_IGN.
        assert callable(handler) and handler is not signal.default_int_handler
        assert supervisor.SUPERVISED_ENV not in os.environ
    finally:
        signal.signal(signal.SIGINT, before[0])
        signal.signal(signal.SIGTERM, before[1])
