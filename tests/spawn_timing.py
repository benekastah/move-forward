"""Throwaway (don't merge): measures starting processes inside the real test
run, loaded with `-p tests.spawn_timing`.

- Counts every process the tests start, by program and git subcommand, and by
  whether the package or the tests started it.
- About halfway through its share of the tests, each worker times each way of
  starting git, interleaved, between two tests, while the other workers are
  still running tests.

The controller prints both in the terminal summary, and appends them to
$GITHUB_STEP_SUMMARY when it's set.
"""

import functools
import inspect
import json
import os
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

ROUNDS = 30
DIR_VARIABLE = "SPAWN_TIMING_DIR"
GLOBAL_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}

HELPER = r"""
import json, struct, subprocess, sys
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
while header := stdin.read(4):
    argv = json.loads(stdin.read(struct.unpack("<I", header)[0]))
    result = subprocess.run(argv, capture_output=True, close_fds=False)
    stdout.write(struct.pack("<iI", result.returncode, len(result.stdout)) + result.stdout)
    stdout.flush()
"""

root = Path.cwd()
counts: Counter[tuple[str, str]] = Counter()
create_ms: dict[str, list[float]] = {}
measuring = False
finished = 0
collected = 0
helper: subprocess.Popen[bytes] | None = None


def command_name(argv: list[str]) -> str:
    program = Path(argv[0]).name.lower().removesuffix(".exe")
    if program != "git":
        return program
    rest = iter(argv[1:])
    for arg in rest:
        if arg in GLOBAL_OPTIONS_WITH_VALUE:
            next(rest, None)
        elif not arg.startswith("-"):
            return f"git {arg}"
    return "git"


def origin() -> str:
    """ "package" if the package's code is anywhere on the stack (the tests
    called it, and it started the process), else "tests"."""
    frame = inspect.currentframe()
    while frame is not None:
        path = Path(frame.f_code.co_filename)
        if (
            path.is_relative_to(root)
            and ".venv" not in path.parts
            and path.relative_to(root).parts[0] != "tests"
        ):
            return "package"
        frame = frame.f_back
    return "tests"


def install_counter() -> None:
    name = "_execute_child"
    original: Callable[..., None] = getattr(subprocess.Popen, name)

    @functools.wraps(original)
    def counted(self: subprocess.Popen[Any], args: Any, *rest: Any, **kwargs: Any) -> None:
        start = time.perf_counter()
        try:
            original(self, args, *rest, **kwargs)
        finally:
            if not measuring:
                items: list[Any] = (
                    [args] if isinstance(args, str | bytes | os.PathLike) else list(args)
                )
                argv = [os.fsdecode(a) if isinstance(a, str | bytes) else str(a) for a in items]
                command = command_name(argv)
                counts[(origin(), command)] += 1
                create_ms.setdefault(command, []).append((time.perf_counter() - start) * 1000)

    setattr(subprocess.Popen, name, counted)


def start_helper() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )


def through_helper(argv: list[str]) -> None:
    assert helper is not None and helper.stdin is not None and helper.stdout is not None
    request = json.dumps(argv).encode()
    helper.stdin.write(struct.pack("<I", len(request)) + request)
    helper.stdin.flush()
    code, length = struct.unpack("<iI", helper.stdout.read(8))
    helper.stdout.read(length)
    assert code == 0


if sys.platform != "win32":

    def posix_spawn_direct(argv: list[str]) -> None:
        read_end, write_end = os.pipe()
        pid = os.posix_spawn(
            argv[0],
            argv,
            os.environ,
            file_actions=[(os.POSIX_SPAWN_DUP2, write_end, 1), (os.POSIX_SPAWN_CLOSE, read_end)],
        )
        os.close(write_end)
        while os.read(read_end, 65536):
            pass
        os.close(read_end)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0


def ways(git: str, git_core: str) -> dict[str, tuple[str, Callable[[list[str]], object]]]:
    """Each way of starting git: (the git it starts, how)."""

    def run(close_fds: bool = True) -> Callable[[list[str]], object]:
        def start(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(argv, capture_output=True, check=True, close_fds=close_fds)

        return start

    found: dict[str, tuple[str, Callable[[list[str]], object]]] = {
        "subprocess.run, git by name": ("git", run()),
        "subprocess.run, full path": (git, run()),
        "subprocess.run, full path, close_fds=False": (git, run(close_fds=False)),
        "subprocess.run, git-core's git, close_fds=False": (git_core, run(close_fds=False)),
        "helper process": (git, through_helper),
    }
    if sys.platform != "win32":
        found["os.posix_spawn"] = (git, posix_spawn_direct)
    return found


def state() -> dict[str, object]:
    """What this worker's process looks like as it starts git."""
    found: dict[str, object] = {"threads": threading.active_count(), "modules": len(sys.modules)}
    if os.path.isdir("/dev/fd"):
        found["open fds"] = len(os.listdir("/dev/fd"))
    if sys.platform != "win32":
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        found["max RSS MB"] = round(maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024))
    return found


def measure() -> dict[str, object]:
    global measuring
    measuring = True
    try:
        git = shutil.which("git") or "git"
        core = subprocess.run(
            [git, "--exec-path"], capture_output=True, text=True, check=True
        ).stdout.strip()
        git_core = os.path.join(core, "git.exe" if os.name == "nt" else "git")
        with tempfile.TemporaryDirectory() as temp:
            identity = ["-c", "user.name=a", "-c", "user.email=a@a"]
            subprocess.run([git, "init", "-q", temp], check=True)
            subprocess.run(
                [git, *identity, "-C", temp, "commit", "-q", "--allow-empty", "-m", "x"], check=True
            )
            commands = {"version": ["--version"], "rev-parse": ["-C", temp, "rev-parse", "HEAD"]}
            times: dict[str, list[float]] = {}
            methods = ways(git, git_core)
            for _ in range(ROUNDS):
                for command, tail in commands.items():
                    for name, (program, start) in methods.items():
                        began = time.perf_counter()
                        start([program, *tail])
                        times.setdefault(f"{command}\t{name}", []).append(
                            (time.perf_counter() - began) * 1000
                        )
        return {"times": times, "state": state()}
    finally:
        measuring = False


def pytest_configure(config: pytest.Config) -> None:
    global helper
    if not hasattr(config, "workerinput") and DIR_VARIABLE not in os.environ:
        # The controller, or a run without workers: before any worker starts,
        # so they inherit it.
        os.environ[DIR_VARIABLE] = tempfile.mkdtemp(prefix="spawn-timing-")
    helper = start_helper()
    install_counter()


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    global finished
    finished += 1
    workers = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    if finished == max(1, collected // workers // 2):
        result = measure()
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        result["finished before"] = finished
        Path(os.environ[DIR_VARIABLE], f"times-{worker}.json").write_text(
            json.dumps(result), encoding="utf-8"
        )


def pytest_collection_finish(session: pytest.Session) -> None:
    global collected
    collected = len(session.items)


def pytest_sessionfinish(session: pytest.Session) -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    rows = [[o, c, n] for (o, c), n in counts.items()]
    Path(os.environ[DIR_VARIABLE], f"counts-{worker}.json").write_text(
        json.dumps({"counts": rows, "create_ms": create_ms}), encoding="utf-8"
    )
    if helper is not None and helper.stdin is not None:
        helper.stdin.close()
        helper.wait()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def report(directory: Path) -> list[str]:
    lines = [
        f"## Starting processes: {sys.platform}, Python {sys.version.split()[0]}, {os.cpu_count()} CPUs",
        "",
    ]
    times: dict[str, list[float]] = {}
    states: list[str] = []
    for path in sorted(directory.glob("times-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        states.append(f"{path.stem}: {data['state']}, after {data['finished before']} tests")
        for key, values in data["times"].items():
            times.setdefault(key, []).extend(values)
    lines += ["Each worker, halfway through its tests:", ""] + [f"- {s}" for s in states] + [""]
    lines += ["| command | method | median ms | p90 ms | samples |", "|---|---|---|---|---|"]
    for key, values in times.items():
        command, method = key.split("\t")
        lines.append(
            f"| {command} | {method} | {statistics.median(values):.2f}"
            f" | {percentile(values, 0.9):.2f} | {len(values)} |"
        )
    counts_total: Counter[tuple[str, str]] = Counter()
    created: dict[str, list[float]] = {}
    for path in directory.glob("counts-*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for o, c, n in data["counts"]:
            counts_total[(o, c)] += n
        for c, values in data["create_ms"].items():
            created.setdefault(c, []).extend(values)
    total = sum(counts_total.values())
    lines += ["", f"### Processes the tests started: {total}", ""]
    lines += [
        "| started by | command | count | share | median ms to create |",
        "|---|---|---|---|---|",
    ]
    for (o, c), n in counts_total.most_common(40):
        lines.append(f"| {o} | {c} | {n} | {n / total:.1%} | {statistics.median(created[c]):.2f} |")
    return lines


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        return
    lines = report(Path(os.environ[DIR_VARIABLE]))
    for line in lines:
        terminalreporter.write_line(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
