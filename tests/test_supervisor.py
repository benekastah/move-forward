"""`mfd serve`'s parent process: replacing, restarting and stopping the server."""

from collections.abc import Callable
from pathlib import Path

import pytest

from move_forward import supervisor


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
        restart()  # the config changed and passes the check
        servers[-1].code = 0  # then it exits cleanly

    monkeypatch.setattr(supervisor, "watch", watch)
    argv = ["mfd", "serve", "--open"]
    assert supervisor.supervise(argv, tmp_path / "c.toml", code=False, popen=popen) == 0
    first, second = servers
    assert first.terminated and not second.terminated
    assert first.command[-2:] == ["serve", "--open"] and second.command[-1] == "serve"
    assert first.env[supervisor.SUPERVISED_ENV] == second.env[supervisor.SUPERVISED_ENV] == "1"


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
