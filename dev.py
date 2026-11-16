"""Set up and check this clone, the same on macOS, Linux and Windows.

    python dev.py venv               # .venv with the package (editable) and the dev tools
    python dev.py check [ARGS...]    # static, then tests (ARGS go to pytest)
    python dev.py static             # format, lint and types: the same on every system
    python dev.py tests [ARGS...]    # the tests, in parallel (ARGS go to pytest)
    python dev.py tests --shard I/N  # the Ith of N even parts of the tests, as CI splits them
    python dev.py coverage [ARGS...] # the tests under coverage.py, and what they don't run
    python dev.py install            # venv, the package's commands on PATH, the git hooks
    python dev.py pre-commit         # what the pre-commit hook runs
    python dev.py pre-push           # what the pre-push hook runs

On Windows, run it with `py` (`py dev.py check`). The hooks in git_hooks/ call it.

`tests` runs pytest with this file as a plugin (`-p dev`), which adds `--shard I/N`: it sorts
the tests by node id and keeps every Nth from the Ith, so the N shards run each test exactly
once between them, and as many tests each, give or take one. CI runs a system's tests in N
jobs this way when the project's Copier answers say so (see AGENTS.md).

It comes from python-template, and `copier update` replaces it: a project adds to it in
dev_project.py, its own file, if it has one. That can define `COMMANDS`, a dict of extra
commands (a name to a function taking the command's arguments, a list of strings), and
`after_install()`, which `install` runs last. It's held to the same rules as this file.
Standard library only: it runs before .venv exists. It runs under any Python from 3.11 (the
first with tomllib; ruff checks it as 3.11), whatever the package needs, so the hooks work
whichever `python3` comes first: everything but making .venv with venv and pip runs .venv's
tools, and uv picks its own Python.
"""

import argparse
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for the plugin's types: pytest is .venv's, not standard library
    import pytest

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WINDOWS = sys.platform == "win32"
BIN = VENV / ("Scripts" if WINDOWS else "bin")
STAMP = VENV / "installed"
"""Touched after installing; older than pyproject.toml or uv.lock means install again."""


def required_python(pyproject: str) -> tuple[int, int]:
    """The oldest Python the package runs on, from pyproject.toml's `requires-python`
    (`>=3.11`)."""
    spec: str = tomllib.loads(pyproject)["project"]["requires-python"]
    major, minor = spec.removeprefix(">=").strip().split(".")[:2]
    return int(major), int(minor)


def scripts(pyproject: str) -> list[str]:
    """The commands the package installs, from pyproject.toml's `[project.scripts]`."""
    return list(tomllib.loads(pyproject)["project"].get("scripts", {}))


def venv_python(pyvenv_cfg: str) -> tuple[int, int] | None:
    """The Python a venv was made with, from its pyvenv.cfg: `version` (venv) or
    `version_info` (uv). None if it names neither."""
    for line in pyvenv_cfg.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("version", "version_info"):
            major, minor = value.strip().split(".")[:2]
            return int(major), int(minor)
    return None


def too_old(running: tuple[int, int], required: tuple[int, int]) -> str | None:
    """Why dev.py can't make .venv with venv and pip under Python `running`, or None if it
    can."""
    if running >= required:
        return None
    want = ".".join(map(str, required))
    have = ".".join(map(str, running))
    return (
        f"dev.py: .venv needs Python {want} or newer, and this is {have}."
        f" Run it with python{want} (on Windows, py -{want}), or install uv, which finds one."
    )


def _project() -> ModuleType | None:
    """dev_project.py, the project's own additions to this script, or None if it has none."""
    if not (ROOT / "dev_project.py").exists():
        return None
    return importlib.import_module("dev_project")


def _pyproject_text() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _running() -> tuple[int, int]:
    return sys.version_info.major, sys.version_info.minor


def tool(name: str) -> str:
    """A command in .venv, e.g. `ruff`."""
    return str(BIN / (f"{name}.exe" if WINDOWS else name))


def run(*args: str) -> None:
    """Run a command; its failure ends this script with its exit code."""
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def venv() -> None:
    """Create .venv with the package (editable) and the dev dependencies, or bring it up to
    date: they're reinstalled whenever pyproject.toml or uv.lock has changed since they were
    last installed, so it's quick when there's nothing to do.

    Uses uv when it's installed: from uv.lock, which it updates when pyproject.toml's
    dependencies change, except in CI (where $CI is set), where an out-of-date uv.lock is an
    error. Without uv, it uses venv and pip (25.1+ for --group), which don't read uv.lock.
    Either makes .venv again when an older Python made it."""
    sources = [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    newest = max(path.stat().st_mtime for path in sources if path.exists())
    if STAMP.exists() and STAMP.stat().st_mtime >= newest:
        return
    print("dev: installing the package and dev dependencies into .venv", file=sys.stderr)
    if shutil.which("uv"):
        run("uv", "sync", "--quiet", *(["--locked"] if os.environ.get("CI") else []))
    else:
        required = required_python(_pyproject_text())
        cfg = VENV / "pyvenv.cfg"
        made_with = venv_python(cfg.read_text(encoding="utf-8")) if cfg.exists() else None
        if made_with is None or made_with < required:
            # .venv is made (again) with the Python running this, so it has to be new enough;
            # checked before removing the old one.
            problem = too_old(_running(), required)
            if problem:
                sys.exit(problem)
            if cfg.exists():
                print("dev: .venv was made with an older Python; making it again", file=sys.stderr)
                shutil.rmtree(VENV)
        if not Path(tool("python")).exists():
            run(sys.executable, "-m", "venv", str(VENV))
        run(tool("python"), "-m", "pip", "install", "--quiet", "--upgrade", "pip")
        run(tool("python"), "-m", "pip", "install", "--quiet", "--editable", ".", "--group", "dev")
    STAMP.touch()


def check(pytest_args: list[str]) -> None:
    """Every check: `static`, then `tests`."""
    static()
    tests(pytest_args)


def static() -> None:
    """The checks that don't depend on the system, so CI runs them once: Ruff's formatting
    (fix with `ruff format`) and linter (`ruff check --fix` fixes what it can), and
    basedpyright in strict mode."""
    venv()
    run(tool("ruff"), "format", "--check", "--quiet")
    run(tool("ruff"), "check", "--quiet")
    run(tool("basedpyright"))


def tests(pytest_args: list[str]) -> None:
    """The tests, spread over the machine's cores (pytest-xdist, through pyproject.toml's
    `addopts`), with this file as a plugin, for `--shard`. pytest imports it as `dev`, which
    works because `run` starts it in this directory, and `python -m` puts the working directory
    on `sys.path`."""
    venv()
    run(tool("python"), "-m", "pytest", "-p", "dev", *pytest_args)


def shard(value: str) -> tuple[int, int]:
    """`--shard`'s value, `I/N`: the Ith of N shards, from 1."""
    index, _, count = value.partition("/")
    if not (index.isdecimal() and count.isdecimal() and 1 <= int(index) <= int(count)):
        raise argparse.ArgumentTypeError(f"{value!r} isn't I/N with 1 <= I <= N, e.g. 2/3")
    return int(index), int(count)


def pytest_addoption(parser: "pytest.Parser") -> None:
    """Add `--shard` to pytest when it loads this file as a plugin (`-p dev`, as `tests`
    does)."""
    parser.addoption(
        "--shard",
        type=shard,
        default=(1, 1),
        metavar="I/N",
        help="run only the Ith of N shards of the tests (see dev.py)",
    )


def pytest_collection_modifyitems(config: "pytest.Config", items: "list[pytest.Item]") -> None:
    """Keep only `--shard`'s tests: of the tests sorted by node id, every Nth from the Ith.
    Every shard collects the same tests, so they split them exactly; the kept ones run in
    pytest's order."""
    index, count = config.option.shard
    if count == 1:
        return
    ordered = sorted(item.nodeid for item in items)
    kept = set(ordered[index - 1 :: count])
    config.hook.pytest_deselected(items=[item for item in items if item.nodeid not in kept])
    items[:] = [item for item in items if item.nodeid in kept]


def coverage(pytest_args: list[str]) -> None:
    """The tests under coverage.py, spread over the cores as in `tests` (pyproject.toml has it
    measure each worker), then each file's lines and branches they didn't run (see
    `[tool.coverage]`); htmlcov/index.html shows them in the source. The workers' data is
    combined into .coverage even when a test fails, for CI's report."""
    venv()
    run(tool("coverage"), "erase")  # an interrupted run's data files would be combined too
    tested = subprocess.run(
        [tool("coverage"), "run", "-m", "pytest", "-q", "-p", "dev", *pytest_args],
        cwd=ROOT,
        check=False,
    )
    combined = subprocess.run([tool("coverage"), "combine", "--quiet"], cwd=ROOT, check=False)
    if tested.returncode != 0 or combined.returncode != 0:
        sys.exit(tested.returncode or combined.returncode)
    run(tool("coverage"), "report")
    run(tool("coverage"), "html", "--quiet")
    print("line by line: htmlcov/index.html")


def _bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def install() -> None:
    """Set up this clone for development and use: .venv, each of the package's commands on
    PATH (a link in ~/.local/bin, or on Windows a `.cmd` there), and this repository's
    hooks. Safe to rerun, e.g. after pyproject.toml changes."""
    venv()
    print(f"set up {VENV}")
    bin_dir = _bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in scripts(_pyproject_text()):
        if WINDOWS:
            shim = bin_dir / f"{name}.cmd"
            shim.write_text(f'@"{tool(name)}" %*\r\n', encoding="utf-8")
            print(f"wrote {shim}")
        else:
            link = bin_dir / name
            if link.exists() and not link.is_symlink():
                sys.exit(
                    f"install: {link} exists and isn't a link; move it out of the way and rerun"
                )
            link.unlink(missing_ok=True)
            link.symlink_to(tool(name))
            print(f"linked {link} -> {tool(name)}")
    run("git", "config", "core.hooksPath", "git_hooks")
    print("set this repository's hooks to git_hooks/")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        if WINDOWS:
            _add_to_user_path(bin_dir)
            print(f"added {bin_dir} to your PATH; open a new terminal to use it")
        else:
            print(f"warning: {bin_dir} isn't on your PATH; add it", file=sys.stderr)
    after_install = getattr(_project(), "after_install", None)
    if after_install is not None:
        after_install()


def _add_to_user_path(folder: Path) -> None:
    """Add `folder` to my user's PATH, as Windows' environment variables dialog would; new
    terminals see it."""
    if sys.platform != "win32":
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in str(current).split(";") if p]
        if str(folder) in parts:
            return
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([*parts, str(folder)]))
    # Tell running programs (Explorer, which starts new terminals) the environment changed.
    import ctypes

    hwnd_broadcast, wm_settingchange, smto_abortifhung = 0xFFFF, 0x001A, 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast, wm_settingchange, 0, "Environment", smto_abortifhung, 5000, None
    )


def _git(*args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=ROOT, input=stdin, capture_output=True, check=True
    ).stdout


def pre_commit() -> None:
    """Format the staged Python files with Ruff and apply its safe lint fixes (e.g. import
    sorting). The staged version of each file is fixed and staged in its place, so a file
    that's only partly staged commits just its staged part, fixed; the working copy is fixed
    too. Lint errors Ruff can't fix stop the commit."""
    staged = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--", "*.py")
    paths = staged.decode().splitlines()
    if not paths:
        return
    venv()
    for path in paths:
        fixed = subprocess.run(
            [tool("ruff"), "check", "--fix", "--quiet", "--stdin-filename", path, "-"],
            cwd=ROOT,
            input=_git("show", f":{path}"),
            capture_output=True,
            check=False,
        )
        if fixed.returncode != 0:
            sys.stderr.write(fixed.stdout.decode() + fixed.stderr.decode())
            sys.exit(fixed.returncode)
        formatted = subprocess.run(
            [tool("ruff"), "format", "--quiet", "--stdin-filename", path, "-"],
            cwd=ROOT,
            input=fixed.stdout,
            capture_output=True,
            check=False,
        )
        if formatted.returncode != 0:  # e.g. a syntax error: stop rather than stage it empty
            sys.stderr.write(formatted.stderr.decode())
            sys.exit(formatted.returncode)
        with tempfile.NamedTemporaryFile(delete=False) as scratch:
            scratch.write(formatted.stdout)
        try:
            blob = _git("hash-object", "-w", scratch.name).decode().strip()
        finally:
            os.unlink(scratch.name)
        mode = _git("ls-files", "--stage", "--", path).decode().split(" ")[0]
        _git("update-index", "--cacheinfo", f"{mode},{blob},{path}")
        if (ROOT / path).is_file():
            run(tool("ruff"), "check", "--fix", "--quiet", "--", path)
            run(tool("ruff"), "format", "--quiet", "--", path)


def pushes_to_main(refs: str) -> bool:
    """Whether a push brings commits to `main`. git passes each ref being pushed on stdin as
    "<local ref> <local sha> <remote ref> <remote sha>"; a deletion's local sha is all zeros."""
    pushed = [line.split() for line in refs.splitlines() if line.split()]
    return any(sha.strip("0") and remote == "refs/heads/main" for _, sha, remote, _ in pushed)


def pre_push() -> None:
    """Run every check before pushing to `main`, which nothing else stands in front of. A
    branch has CI, on every system. Skip it with `git push --no-verify`."""
    if pushes_to_main(sys.stdin.read()):
        check(["-q"])


def main(argv: list[str]) -> None:
    command, *rest = argv or ["help"]
    if command == "venv":
        venv()
    elif command == "check":
        check(rest)
    elif command == "static":
        static()
    elif command == "tests":
        tests(rest)
    elif command == "coverage":
        coverage(rest)
    elif command == "install":
        install()
    elif command == "pre-commit":
        pre_commit()
    elif command == "pre-push":
        pre_push()
    else:
        commands: dict[str, Callable[[list[str]], None]] = getattr(_project(), "COMMANDS", {})
        if command not in commands:
            sys.exit((__doc__ or "") + "".join(f"    python dev.py {name}\n" for name in commands))
        commands[command](rest)


if __name__ == "__main__":
    main(sys.argv[1:])
