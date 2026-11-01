"""Set up and check this clone, the same on macOS, Linux and Windows.

    python dev.py venv               # .venv with the package (editable) and the dev tools
    python dev.py check [ARGS...]    # format, lint, types, then the tests (ARGS go to pytest)
    python dev.py coverage [ARGS...] # the tests under coverage.py, and what they don't run
    python dev.py install            # venv, `mfd` on PATH, the git hooks, then `mfd init`
    python dev.py pre-commit         # what the pre-commit hook runs
    python dev.py pre-push           # what the pre-push hook runs

On Windows, run it with `py` (`py dev.py check`). The hooks in git_hooks/ call it.
Standard library only: it runs before .venv exists.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WINDOWS = sys.platform == "win32"
BIN = VENV / ("Scripts" if WINDOWS else "bin")
STAMP = VENV / "installed"
"""Touched after installing; older than pyproject.toml means install again."""


def tool(name: str) -> str:
    """A command in .venv, e.g. `ruff`."""
    return str(BIN / (f"{name}.exe" if WINDOWS else name))


def run(*args: str) -> None:
    """Run a command; its failure ends this script with its exit code."""
    result = subprocess.run(args, cwd=ROOT, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def venv() -> None:
    """Create .venv with the package (editable) and the dev dependencies from
    pyproject.toml, or bring it up to date: they're reinstalled whenever pyproject.toml has
    changed since they were last installed, so it's quick when there's nothing to do. Uses
    uv when it's installed, else venv and pip (25.1+ for --group)."""
    if STAMP.exists() and STAMP.stat().st_mtime >= (ROOT / "pyproject.toml").stat().st_mtime:
        return
    print("dev: installing the package and dev dependencies into .venv", file=sys.stderr)
    if shutil.which("uv"):
        run("uv", "sync", "--quiet")
    else:
        if not Path(tool("python")).exists():
            run(sys.executable, "-m", "venv", str(VENV))
        run(tool("python"), "-m", "pip", "install", "--quiet", "--upgrade", "pip")
        run(tool("python"), "-m", "pip", "install", "--quiet", "--editable", ".", "--group", "dev")
    STAMP.touch()


def check(pytest_args: list[str]) -> None:
    """Every check: Ruff's formatting (fix with `ruff format`) and linter (`ruff check --fix`
    fixes what it can), basedpyright in strict mode, then the tests."""
    venv()
    run(tool("ruff"), "format", "--check", "--quiet")
    run(tool("ruff"), "check", "--quiet")
    run(tool("basedpyright"))
    run(tool("python"), "-m", "pytest", *pytest_args)


def coverage(pytest_args: list[str]) -> None:
    """The tests under coverage.py, then each file's lines and branches they didn't run
    (see `[tool.coverage]` in pyproject.toml); htmlcov/index.html shows them in the source."""
    venv()
    run(tool("coverage"), "run", "-m", "pytest", "-q", *pytest_args)
    run(tool("coverage"), "report")
    run(tool("coverage"), "html", "--quiet")
    print("line by line: htmlcov/index.html")


def _bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def install() -> None:
    """Set up this clone for development and use: .venv, the `mfd` command on PATH (a link
    in ~/.local/bin, or on Windows a `mfd.cmd` there), and this repository's hooks. Safe to
    rerun, e.g. after pyproject.toml changes."""
    venv()
    print(f"set up {VENV}")
    bin_dir = _bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        shim = bin_dir / "mfd.cmd"
        shim.write_text(f'@"{tool("mfd")}" %*\r\n')
        print(f"wrote {shim}")
    else:
        link = bin_dir / "mfd"
        if link.exists() and not link.is_symlink():
            sys.exit(f"install: {link} exists and isn't a link; move it out of the way and rerun")
        link.unlink(missing_ok=True)
        link.symlink_to(tool("mfd"))
        print(f"linked {link} -> {tool('mfd')}")
    run("git", "config", "core.hooksPath", "git_hooks")
    print("set this repository's hooks to git_hooks/")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        if sys.platform == "win32":
            _add_to_user_path(bin_dir)
            print(f"added {bin_dir} to your PATH; open a new terminal for `mfd`")
        else:
            print(f"warning: {bin_dir} isn't on your PATH; add it", file=sys.stderr)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    config = Path(base) / "move-forward" / "config.toml"
    if config.exists():
        print(f"config: {config}")
    elif sys.stdin.isatty():
        print(f"\nNo config yet; let's write {config}.")
        run(tool("mfd"), "init")
    else:
        print(f"no config at {config}; run `mfd init` to write one")


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


def pre_push() -> None:
    """Run every check before pushing, unless the push only deletes remote branches. git
    passes each ref being pushed on stdin as "<local ref> <local sha> <remote ref> <remote
    sha>"; a deletion's local sha is all zeros. Skip it with `git push --no-verify`."""
    pushes_commits = any(
        set(line.split()[1]) != {"0"} for line in sys.stdin.read().splitlines() if line.split()
    )
    if pushes_commits:
        check(["-q"])


def main(argv: list[str]) -> None:
    command, *rest = argv or ["help"]
    if command == "venv":
        venv()
    elif command == "check":
        check(rest)
    elif command == "coverage":
        coverage(rest)
    elif command == "install":
        install()
    elif command == "pre-commit":
        pre_commit()
    elif command == "pre-push":
        pre_push()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
