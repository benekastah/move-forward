"""Set up and check this clone, the same on macOS, Linux and Windows.

    python dev.py venv               # .venv with the package (editable) and the dev tools
    python dev.py check [ARGS...]    # static, then tests (ARGS go to pytest)
    python dev.py static             # format, lint, types and man pages, the same everywhere
    python dev.py tests [ARGS...]    # the tests, in parallel (ARGS go to pytest)
    python dev.py tests --shard I/N  # the Ith of N even parts of the tests, as CI splits them
    python dev.py coverage [ARGS...] # the tests under coverage.py, and what they don't run
    python dev.py install            # venv, the package's commands on PATH, the git hooks
    python dev.py install --replace  # ...taking the commands over from another clone
    python dev.py man [--check]      # man/<command>.1 from README.md (--check: fail if stale)
    python dev.py pre-commit         # what the pre-commit hook runs

On Windows, run it with `py` (`py dev.py check`). The hooks in git_hooks/ call it.

`static` runs every check even when one fails, and `check` runs the tests even when `static`
fails, so one run reports every failure; the exit status is the first failure's. `venv` says so
when it changes uv.lock, which should be committed with the pyproject.toml change behind it.

`tests` runs pytest with this file as a plugin (`-p dev`), which adds `--shard I/N`: it sorts
the tests by node id and keeps every Nth from the Ith, so the N shards run each test exactly
once between them, and as many tests each, give or take one. CI runs a system's tests in N
jobs this way when the project's Copier answers say so (see AGENTS.md).

`man` makes each of the package's commands a man page, `man/<command>.1`, from README.md,
with pandoc, exactly the version `PANDOC_VERSION` names (the check compares its exact output, so
another version, e.g. a newer one on PATH, would call the pages stale): dev.py downloads it into
.venv on first use, checking the archive against the checksum it pins. NAME (the command, and
pyproject.toml's description), SYNOPSIS if dev_project.py gives one, DESCRIPTION (the README's
opening), then the README's `##` sections, but for Install, Installing and Development.
`static` checks the pages are current, and the pre-commit hook makes them again from the staged
README when a commit changes what they're made from, and stages them (or, if the commit names its
files, stops it, as it can't). `install` links them into ~/.local/share/man/man1 (not on Windows,
which has no `man`). A package with no commands, a library, gets none.

It comes from python-template, and `copier update` replaces it: a project adds to it in
dev_project.py, its own file, if it has one. That can define `COMMANDS`, a dict of extra
commands (a name to a function taking the command's arguments, a list of strings),
`install_links()`, more links for `install` to make (a list of (link, target) paths: a symlink,
which Windows usually can't make, or a shim if the link ends in `.cmd`), and
`after_install()`, which `install` runs last. For the man pages, it can set
`MAN_SKIPPED_SECTIONS`, the `##` sections to leave out (a set of their titles, in place of
`MAN_SKIPPED_SECTIONS` here), and `MAN_SYNOPSIS`, a page's SYNOPSIS (a dict of a command to its
Markdown). It's held to the same rules as this file.

`install` makes its links only if it may make all of them, and changes nothing if not: it won't
take one over from another clone (or install) whose target still exists, unless told to with
`--replace`, and won't install a clone in a temporary directory (the system's, /tmp or
/var/tmp) at all, which would leave the user's commands broken once it's deleted. So
`after_install()` makes no links of its own: links go in `install_links()`, which `install`
checks with its own.

Standard library only: it runs before .venv exists. It runs under any Python from 3.11 (the
first with tomllib; ruff checks it as 3.11), whatever the package needs, so the hooks work
whichever `python3` comes first: everything but making .venv with venv and pip runs .venv's
tools, and uv picks its own Python.
"""

import argparse
import contextlib
import hashlib
import http.client
import importlib
import io
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
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
PAGES = ROOT / "man"
"""Where `man` writes each command's man page."""
PANDOC_VERSION = "3.14"
"""The pandoc `man` makes the pages with, exactly. To move to another, set this, and each checksum
below to its archive's: `gh api repos/jgm/pandoc/releases/tags/<version> -q '.assets[] | .name +
" " + .digest'`; then make the pages again (`man`) and commit them."""
PANDOC_ARCHIVES: dict[tuple[str, str], tuple[str, str]] = {
    ("linux", "x86_64"): (
        f"pandoc-{PANDOC_VERSION}-linux-amd64.tar.gz",
        "c5f4720cdefb3f7bdc5d07202ef579feef0d1370773c873804dd8fdaa8c4f1b8",
    ),
    ("linux", "arm64"): (
        f"pandoc-{PANDOC_VERSION}-linux-arm64.tar.gz",
        "c692f3d78a415741bbbac4cdbbbe26fa7edca1f43d2301b982fbf4009cc8cf7f",
    ),
    ("darwin", "x86_64"): (
        f"pandoc-{PANDOC_VERSION}-x86_64-macOS.zip",
        "891708579061e7b1c1a6ce0df30ebb3cb2b6d0ca045ed3c8554ab830c1e0c270",
    ),
    ("darwin", "arm64"): (
        f"pandoc-{PANDOC_VERSION}-arm64-macOS.zip",
        "58d6593884fec9d4424b641b92503c316d9c36decb2891ff8d73ae74dd3b6304",
    ),
    # pandoc has no Windows ARM build: that runs the x86-64 one.
    ("win32", "x86_64"): (
        f"pandoc-{PANDOC_VERSION}-windows-x86_64.zip",
        "0144b96e8c7b023fd382ec5ef5bdc475e873f1019d6fa72502492df2c9a94a3b",
    ),
    ("win32", "arm64"): (
        f"pandoc-{PANDOC_VERSION}-windows-x86_64.zip",
        "0144b96e8c7b023fd382ec5ef5bdc475e873f1019d6fa72502492df2c9a94a3b",
    ),
}
"""The archive of pandoc's release to download, and its SHA-256, for each (system, CPU)."""
PANDOC_MAX_BYTES = 100_000_000
"""The most `_download` takes of a download. A pandoc archive is about 45 MB: `_download` reads one
byte more than this, to see a download is too big, and refuses it."""
MAN_SOURCES = ["README.md", "pyproject.toml", "dev.py", "dev_project.py"]
"""What the man pages are made from: a commit changing one makes them again."""
MAN_SKIPPED_SECTIONS = {"Install", "Installing", "Development"}
"""The README's `##` sections a man page leaves out, unless dev_project.py sets its own."""


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


def pandoc_platform() -> tuple[str, str]:
    """This system ("linux", "darwin" or "win32") and CPU ("x86_64" or "arm64"), as PANDOC_ARCHIVES
    names them."""
    system = "linux" if sys.platform.startswith("linux") else sys.platform
    machine = platform.machine().lower()
    return system, {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)


def pandoc_archive() -> tuple[str, str]:
    """The name and SHA-256 of the pandoc archive for this system."""
    key = pandoc_platform()
    if key not in PANDOC_ARCHIVES:
        sys.exit(f"man: dev.py has no pandoc {PANDOC_VERSION} to download for {key[0]} on {key[1]}")
    return PANDOC_ARCHIVES[key]


def pandoc() -> str:
    """The path of pandoc `PANDOC_VERSION`, which `install_pandoc` puts in .venv on first use (and
    which making .venv again removes)."""
    directory = VENV / "pandoc" / PANDOC_VERSION
    executable = directory / ("pandoc.exe" if WINDOWS else "pandoc")
    if not executable.is_file():
        install_pandoc(directory)
    return str(executable)


def install_pandoc(directory: Path) -> None:
    """Download this system's pandoc archive and put its executable in `directory`, or end this
    script, without having made it, if the archive isn't the one PANDOC_ARCHIVES pins. The
    standard library unpacks the archive, and only its executable is taken out of it."""
    name, sha256 = pandoc_archive()
    url = f"https://github.com/jgm/pandoc/releases/download/{PANDOC_VERSION}/{name}"
    print(f"dev: downloading {url}", file=sys.stderr)
    data = _download(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        sys.exit(f"man: the checksum of {url} is {actual}, not the {sha256} dev.py pins for it")
    executable = "pandoc.exe" if WINDOWS else "pandoc"
    directory.parent.mkdir(parents=True, exist_ok=True)
    # Made beside the directory, and moved into place, so one that exists has its executable.
    # ignore_cleanup_errors: a virus scanner holding a file there on Windows mustn't fail an install
    # that has finished.
    with tempfile.TemporaryDirectory(dir=directory.parent, ignore_cleanup_errors=True) as scratch:
        staged = Path(scratch) / "pandoc"
        staged.mkdir()
        (staged / executable).write_bytes(_archived(name, data, executable))
        (staged / executable).chmod(0o755)
        if directory.exists() and not (directory / executable).is_file():
            # What a crash left, which has no executable: moved aside rather than deleted. (Two runs
            # may find it at once; if one then installs first, the other keeps that install below.)
            with contextlib.suppress(OSError):
                directory.replace(Path(scratch) / "broken")
        try:
            staged.replace(directory)
        except OSError:
            # Another run installed it first (the pre-push hook and a manual run, say): use theirs.
            if not (directory / executable).is_file():
                raise


def _download(url: str) -> bytes:
    """What `url` serves, at most `PANDOC_MAX_BYTES` of it: a failure that may pass (no network, a
    server error) is tried again, and any other (a 404, a certificate that doesn't verify) or a
    download too big ends this script."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # the URL is https
                data = response.read(PANDOC_MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 2:
                sys.exit(f"man: couldn't download {url}: {error}")
        # No network, a certificate, a dropped connection, a timeout:
        except (OSError, http.client.HTTPException) as error:
            reason = getattr(error, "reason", None)
            if attempt == 2 or isinstance(reason, ssl.SSLCertVerificationError):
                sys.exit(f"man: couldn't download {url}: {error}")
        else:
            if len(data) > PANDOC_MAX_BYTES:
                sys.exit(f"man: {url} is bigger than the {PANDOC_MAX_BYTES} bytes dev.py allows")
            return data
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _archived(name: str, data: bytes, executable: str) -> bytes:
    """The file called `executable` in the zip or tar archive `data` (`name` says which), at any
    depth."""
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if not info.is_dir() and info.filename.rsplit("/", 1)[-1] == executable:
                    return archive.read(info)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar:
                if member.isfile() and member.name.rsplit("/", 1)[-1] == executable:
                    file = tar.extractfile(member)
                    if file is not None:
                        return file.read()
    sys.exit(f"man: {name} has no {executable}")


def _pyproject_text() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _running() -> tuple[int, int]:
    return sys.version_info.major, sys.version_info.minor


def tool(name: str) -> str:
    """A command in .venv, e.g. `ruff`."""
    return str(BIN / (f"{name}.exe" if WINDOWS else name))


def run(*args: str) -> None:
    """Run a command; its failure ends this script with its exit code."""
    code = run_code(*args)
    if code != 0:
        sys.exit(code)


def run_code(*args: str) -> int:
    """Run a command and return its exit code, for a caller that goes on after a failure."""
    return subprocess.run(args, cwd=ROOT, check=False).returncode


def venv() -> None:
    """Create .venv with the package (editable) and the dev dependencies, or bring it up to
    date: they're reinstalled whenever pyproject.toml or uv.lock has changed since they were
    last installed, so it's quick when there's nothing to do.

    Uses uv when it's installed: from uv.lock, which it updates when pyproject.toml's
    dependencies change, except in CI (where $CI is set), where an out-of-date uv.lock is an
    error. Without uv, it uses venv and pip (25.1+ for --group), which don't read uv.lock.
    Either makes .venv again when an older Python made it.

    Says so when uv changed uv.lock (compared by content, so a rewrite to the same text is
    silent): it should be committed with the pyproject.toml change that caused it."""
    sources = [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    newest = max(path.stat().st_mtime for path in sources if path.exists())
    if STAMP.exists() and STAMP.stat().st_mtime >= newest:
        return
    print("dev: installing the package and dev dependencies into .venv", file=sys.stderr)
    if shutil.which("uv"):
        lock = ROOT / "uv.lock"
        before = lock.read_bytes() if lock.exists() else None
        run("uv", "sync", "--quiet", *(["--locked"] if os.environ.get("CI") else []))
        after = lock.read_bytes() if lock.exists() else None
        if after != before:
            what = "created" if before is None else "changed"
            print(f"dev: uv.lock {what}; commit it with pyproject.toml", file=sys.stderr)
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
    """Every check: `static`, then `tests`, which runs even if `static` failed, so one run
    reports everything; the exit status is the first failure's."""
    venv()
    static_code = _static()
    tests_code = _tests(pytest_args)
    if static_code or tests_code:
        sys.exit(static_code or tests_code)


def static() -> None:
    """The checks that don't depend on the system, so CI runs them once: Ruff's formatting
    (fix with `ruff format`) and linter (`ruff check --fix` fixes what it can), basedpyright
    in strict mode, and that the man pages are current (`man --check`). Runs all of them even
    if one fails, then exits with the first failure's code, so one run reports every failure."""
    venv()
    code = _static()
    if code:
        sys.exit(code)


def _static() -> int:
    """`static`'s checks, in .venv, as the first failing one's exit code (0 if none failed)."""
    codes = [
        run_code(tool("ruff"), "format", "--check", "--quiet"),
        run_code(tool("ruff"), "check", "--quiet"),
        run_code(tool("basedpyright")),
        _man_check(),
    ]
    return next((code for code in codes if code), 0)


def tests(pytest_args: list[str]) -> None:
    """The tests, spread over the machine's cores (pytest-xdist, through pyproject.toml's
    `addopts`), with this file as a plugin, for `--shard`. pytest imports it as `dev`, which
    works because `run` starts it in this directory, and `python -m` puts the working directory
    on `sys.path`."""
    venv()
    code = _tests(pytest_args)
    if code:
        sys.exit(code)


def _tests(pytest_args: list[str]) -> int:
    """`tests`' pytest run, in .venv, as its exit code."""
    return run_code(tool("python"), "-m", "pytest", "-p", "dev", *pytest_args)


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


MAN_FILTER = """
-- dev.py's pandoc filter, which makes a man page of the README: NAME, SYNOPSIS (if given) and
-- DESCRIPTION (the README's opening) first, then the README's ## sections as the page's (but
-- for those skipped), their ### ones as subsections, and links within the README as plain
-- text. dev.py passes the rest as JSON in $DEV_MAN_PAGE.
local page = pandoc.json.decode(os.getenv("DEV_MAN_PAGE"), false)

function Pandoc(doc)
  local blocks = pandoc.List()
  local function section(title, markdown)
    blocks:insert(pandoc.Header(1, title))
    blocks:extend(pandoc.read(markdown, "markdown").blocks)
  end
  section("NAME", page.name)
  if page.synopsis ~= "" then
    section("SYNOPSIS", page.synopsis)
  end
  section("DESCRIPTION", "")
  local skipped = {}
  for _, title in ipairs(page.skipped) do
    skipped[title] = true
  end
  local skipping = false
  for _, block in ipairs(doc.blocks) do
    if block.t == "Header" and block.level == 1 then
      -- The README's title; the page's header names it instead.
    elseif block.t == "Header" and block.level == 2 then
      local title = pandoc.utils.stringify(block)
      skipping = skipped[title] or false
      if not skipping then
        blocks:insert(pandoc.Header(1, pandoc.text.upper(title)))
      end
    elseif not skipping then
      if block.t == "Header" then
        block.level = block.level - 1
      end
      blocks:insert(block)
    end
  end
  doc.blocks = blocks
  doc.meta.title = page.title
  doc.meta.section = "1"
  doc.meta.header = page.header
  doc.meta.footer = page.footer
  return doc
end

function Link(link)
  if link.target:sub(1, 1) == "#" then
    return link.content
  end
end
"""


def man(args: list[str]) -> None:
    """Make each of the package's commands a man page, `man/<command>.1`, from README.md (see
    `pages`), and remove man/'s other `.1` files; with `--check`, change nothing, and fail if
    any of them is out of date."""
    parser = argparse.ArgumentParser(prog="dev.py man", description=man.__doc__)
    parser.add_argument(
        "--check", action="store_true", help="change nothing; fail if a page is out of date"
    )
    if parser.parse_args(args).check:
        code = _man_check()
        if code:
            sys.exit(code)
        return
    made = pages(ROOT / "README.md")
    _write_pages(made)
    for stray in _stray_pages(made):
        stray.unlink()
        print(f"removed {_relative(stray)}, no command's page")


def _man_check() -> int:
    """`man --check`: 1, saying why, if a page is missing or out of date, or man/ has one no
    command has; else 0."""
    made = pages(ROOT / "README.md")
    stale = [page for page, text in made.items() if not page.exists() or page.read_bytes() != text]
    why = [f"man: {_relative(page)} is out of date with README.md" for page in stale]
    why += [f"man: {_relative(page)} is no command's page" for page in _stray_pages(made)]
    if not why:
        return 0
    print("\n".join([*why, "man: run `python3 dev.py man` to make them again"]), file=sys.stderr)
    return 1


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _stray_pages(made: dict[Path, bytes]) -> list[Path]:
    return sorted(page for page in PAGES.glob("*.1") if page not in made)


def _write_pages(made: dict[Path, bytes]) -> None:
    for page, text in made.items():
        if page.exists() and page.read_bytes() == text:
            continue
        page.parent.mkdir(exist_ok=True)
        page.write_bytes(text)
        print(f"wrote {_relative(page)}")


def pages(readme: Path) -> dict[Path, bytes]:
    """Each of the package's commands' man page, `man/<command>.1`, made from `readme` (README.md,
    or a copy of the staged one) with pandoc and `MAN_FILTER`: NAME, the command and
    pyproject.toml's description (see `man_description`); SYNOPSIS, if dev_project.py's
    `MAN_SYNOPSIS` has one for it; DESCRIPTION, the README's opening; then the README's `##`
    sections but `MAN_SKIPPED_SECTIONS` (or dev_project.py's). The page's header is the
    project's name, as in "Git Stack Manual". None at all for a package with no commands."""
    project = tomllib.loads(_pyproject_text())["project"]
    names = list(project.get("scripts", {}))
    if not names:
        return {}
    venv()
    own = _project()
    synopses: dict[str, str] = getattr(own, "MAN_SYNOPSIS", {})
    skipped: set[str] = getattr(own, "MAN_SKIPPED_SECTIONS", MAN_SKIPPED_SECTIONS)
    description = man_description(project.get("description", ""))
    made: dict[Path, bytes] = {}
    with tempfile.TemporaryDirectory() as scratch:
        lua = Path(scratch) / "man.lua"
        lua.write_text(MAN_FILTER, encoding="utf-8")
        for name in names:
            page = {
                "name": f"{name} - {description}" if description else name,
                "synopsis": synopses.get(name, ""),
                "skipped": sorted(skipped),
                "title": name.upper(),
                "header": " ".join(w.capitalize() for w in project["name"].split("-")) + " Manual",
                "footer": project["name"],
            }
            made[PAGES / f"{name}.1"] = _output(
                pandoc(),
                "--from=gfm",
                "--to=man",
                "--standalone",
                "--eol=lf",
                f"--lua-filter={lua}",
                str(readme),
                env={**os.environ, "DEV_MAN_PAGE": json.dumps(page)},
            )
    return made


def man_description(description: str) -> str:
    """pyproject.toml's description as a man page's NAME line has it: without a final period,
    and starting lowercase unless its first word has a capital or a digit past its first letter
    ("Stacked branches." is "stacked branches", "Command-line tool" is "command-line tool";
    "PRs", "I/O" and "X11" stay)."""
    description = description.strip().removesuffix(".")
    word = description.split(" ", 1)[0]
    if word[:1].isupper() and not any(c.isupper() or c.isdigit() for c in word[1:]):
        return description[0].lower() + description[1:]
    return description


def _output(*args: str, env: dict[str, str] | None = None) -> bytes:
    """A command's output; its failure ends this script with its exit code, after its
    errors."""
    done = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, check=False)
    if done.returncode != 0:
        sys.stderr.write(done.stderr.decode(errors="replace"))
        sys.exit(done.returncode)
    return done.stdout


def _bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def _man_dir() -> Path:
    return Path.home() / ".local" / "share" / "man" / "man1"


def install(args: list[str]) -> None:
    """Set up this clone for development and use: .venv, each of the package's commands on
    PATH (a link in ~/.local/bin, or on Windows a `.cmd` there), their man pages where `man`
    looks (a link in ~/.local/share/man/man1, but not on Windows), dev_project.py's
    `install_links()`, and this repository's hooks. Safe to rerun, e.g. after pyproject.toml
    changes.

    Refuses, before changing anything, if a link it would make runs another clone's (or
    install's) file that still exists, unless `--replace` says to take it over; if any is a
    file it didn't make; or, on Windows, if ~/.local/bin has a command's `.exe` (e.g. from
    `uv tool install`), which Windows would run instead of the `.cmd`. Refuses outright in a
    temporary clone (see `_temporary`)."""
    parser = argparse.ArgumentParser(prog="dev.py install", description=install.__doc__)
    parser.add_argument(
        "--replace", action="store_true", help="take over links another clone or install has"
    )
    replace: bool = parser.parse_args(args).replace
    names = scripts(_pyproject_text())
    temporary = _temporary(names)
    if temporary:
        sys.exit(temporary)
    bin_dir = _bin_dir()
    links = [(bin_dir / (f"{name}.cmd" if WINDOWS else name), Path(tool(name))) for name in names]
    man_pages = [] if WINDOWS else [p for p in (PAGES / f"{n}.1" for n in names) if p.exists()]
    links += [(_man_dir() / page.name, page) for page in man_pages]
    install_links: Callable[[], list[tuple[Path, Path]]] | None = getattr(
        _project(), "install_links", None
    )
    if install_links is not None:
        links += install_links()
    refusals = [why for link, _ in links if (why := refusal(link, replace))]
    if WINDOWS:  # e.g. from `uv tool install`, which Windows would run rather than the `.cmd`
        refusals += [
            f"{exe} is another install's, which Windows runs before {exe.stem}.cmd; uninstall it"
            for exe in (bin_dir / f"{name}.exe" for name in names)
            if exe.exists()
        ]
    if refusals:
        sys.exit("\n".join(f"install: {why}" for why in refusals))
    venv()
    print(f"set up {VENV}")
    for link, target in links:
        _link(link, target)
    run("git", "config", "core.hooksPath", "git_hooks")
    print("set this repository's hooks to git_hooks/")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        if WINDOWS:
            _add_to_user_path(bin_dir)
            print(f"added {bin_dir} to your PATH; open a new terminal to use it")
        else:
            print(f"warning: {bin_dir} isn't on your PATH; add it", file=sys.stderr)
    if man_pages and not _man_finds(man_pages[0].stem):
        print(
            f"warning: man doesn't look in {_man_dir().parent}, so it won't find"
            f' {man_pages[0].stem}\'s page; add export MANPATH="$HOME/.local/share/man:" to your'
            " shell's startup file",
            file=sys.stderr,
        )
    after_install = getattr(_project(), "after_install", None)
    if after_install is not None:
        after_install()


def _man_finds(name: str) -> bool:
    """Whether `man` finds `name`'s page."""
    if shutil.which("man") is None:
        return False
    return subprocess.run(["man", "-w", name], capture_output=True, check=False).returncode == 0


def _temporary(names: list[str]) -> str | None:
    """Why `install` won't install this clone, if it's in the temporary directory (or, outside
    Windows, /tmp or /var/tmp, which $TMPDIR may not be, e.g. on macOS), or None: whatever made
    it there will delete it, leaving the user's commands broken."""
    dirs = [tempfile.gettempdir(), *([] if WINDOWS else ["/tmp", "/var/tmp"])]
    temps = [Path(d).resolve() for d in dirs]
    temp = next((d for d in temps if ROOT.is_relative_to(d)), None)
    if temp is None:
        return None
    run_one = f", e.g. {tool(names[0])}" if names else ""
    return (
        f"install: {ROOT} is in a temporary directory ({temp}), so it isn't installed:"
        f" it would take your commands over, then leave them broken once it's deleted. To use"
        f" this clone without installing it, run `python dev.py venv` (and, for its hooks,"
        f" `git config core.hooksPath git_hooks`), then its commands in {BIN}{run_one}."
    )


SHIM = re.compile(r'@"(?P<target>[^"]+)" %\*\s*')
"""A `.cmd` that `install` wrote, which runs `target`."""


def refusal(link: Path, replace: bool) -> str | None:
    """Why `install` won't point `link` at this clone, or None if it may (see `install`): a
    link, or a `.cmd` shim it wrote, that runs a file outside this clone that still exists,
    unless `replace`; or a file that's neither. A link whose file is gone (its clone deleted)
    is taken over."""
    if link.is_symlink():
        current = link.parent / link.readlink()
    elif not link.exists():
        return None
    elif link.suffix == ".cmd" and (
        shim := SHIM.fullmatch(link.read_text(encoding="utf-8", errors="replace"))
    ):
        current = Path(shim["target"])
    else:
        return f"{link} exists, and install didn't make it; move it out of the way and rerun"
    if replace or not current.exists() or current.resolve().is_relative_to(ROOT):
        return None
    return (
        f"{link} points at {current.resolve()}, another clone's or install's, not this one's"
        f" ({ROOT}); rerun with --replace to take it over"
    )


def _link(link: Path, target: Path) -> None:
    """Point `link` at `target`: with a `.cmd` that runs it, if `link` is one, or else a
    symlink."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.suffix == ".cmd":
        link.write_text(f'@"{target}" %*\r\n', encoding="utf-8")
        print(f"wrote {link}")
    else:
        link.unlink(missing_ok=True)
        link.symlink_to(target)
        print(f"linked {link} -> {target}")


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
    """Fix the staged Python files (see `_pre_commit_python`), then make the man pages again
    if the commit changes what they're made from (see `_pre_commit_pages`)."""
    _pre_commit_python()
    _pre_commit_pages()


def _pre_commit_pages() -> None:
    """Make the man pages again, from the staged README.md, if the commit changes one of
    `MAN_SOURCES`, and stage them, so they're current in the commit even if only part of the
    README is staged. Only the README is read from the index: the commands, the description
    and dev_project.py's settings come from the working tree, so a partly staged
    pyproject.toml or dev_project.py can leave the commit's pages stale, as can a page no
    command has; `static`'s check catches both.

    Stops the commit if a page changes and the commit names its files (`git commit PATH`):
    git then runs the hook on an index of its own, and keeps the page staged there out of the
    real one, which would be left staging the old page."""
    if not _git("diff", "--cached", "--name-only", "--", *MAN_SOURCES).strip():
        return
    with tempfile.TemporaryDirectory() as scratch:
        readme = Path(scratch) / "README.md"
        readme.write_bytes(_git("show", ":README.md"))
        made = pages(readme)
    changed = [page for page, text in made.items() if _staged(page) != text]
    if not changed:
        return
    if _temporary_index():
        sys.exit(
            "pre-commit: the man pages need making again, which a commit naming its files"
            " can't stage: run `dev.py man`, `git add` the pages and the files, then commit"
            " without naming files"
        )
    _write_pages(made)
    _git("add", "--", *(_relative(page) for page in changed))


def _staged(path: Path) -> bytes | None:
    """The staged content of `path`, or None if it isn't staged."""
    shown = subprocess.run(
        ["git", "show", f":{_relative(path)}"], cwd=ROOT, capture_output=True, check=False
    )
    return shown.stdout if shown.returncode == 0 else None


def _temporary_index() -> bool:
    """Whether git runs this hook on a temporary index, as for `git commit PATH`, rather than
    the repository's own (or its lock, which becomes it, as for `git commit -a`)."""
    index = os.environ.get("GIT_INDEX_FILE")
    if index is None:
        return False
    # The git directory's index: `--git-path index` would name $GIT_INDEX_FILE.
    own = (Path(_git("rev-parse", "--absolute-git-dir").decode().strip()) / "index").resolve()
    return (ROOT / index).resolve() not in (own, own.with_name(own.name + ".lock"))


def _pre_commit_python() -> None:
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
        install(rest)
    elif command == "man":
        man(rest)
    elif command == "pre-commit":
        pre_commit()
    else:
        commands: dict[str, Callable[[list[str]], None]] = getattr(_project(), "COMMANDS", {})
        if command not in commands:
            sys.exit((__doc__ or "") + "".join(f"    python dev.py {name}\n" for name in commands))
        commands[command](rest)


if __name__ == "__main__":
    main(sys.argv[1:])
