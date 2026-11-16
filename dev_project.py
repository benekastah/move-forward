"""move-forward's own additions to dev.py (see its docstring): `install` ends by writing a
config with `mfd init` when there's none yet, and `within` holds CI's steps to a time limit.

    python dev.py within SECONDS COMMAND...  # COMMAND, failing if it takes longer (CI's limits)

Held to dev.py's rules: standard library only, and Python 3.11 syntax.
"""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import dev


def config_path() -> Path:
    """Where mfd reads its config: $XDG_CONFIG_HOME/move-forward/config.toml, or under
    ~/.config without it."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "move-forward" / "config.toml"


def after_install() -> None:
    """Say where the config is, or write one with `mfd init`: run in a terminal, since it
    asks questions; otherwise just say to."""
    config = config_path()
    if config.exists():
        print(f"config: {config}")
    elif sys.stdin.isatty():
        print(f"\nNo config yet; let's write {config}.")
        dev.run(dev.tool("mfd"), "init")
    else:
        print(f"no config at {config}; run `mfd init` to write one")


def within(args: list[str]) -> None:
    """`within SECONDS COMMAND...`: run a command of dev.py, then fail if it took longer than
    SECONDS: CI's time limits, in check.yml (see AGENTS.md)."""
    if len(args) < 2 or not args[0].isdigit():
        sys.exit("usage: python dev.py within SECONDS COMMAND...")
    seconds = int(args[0])
    start = time.monotonic()
    dev.main(args[1:])
    took = time.monotonic() - start
    print(f"dev: took {took:.1f}s, of {seconds}s")
    if took > seconds:
        print(
            f"::error::took {took:.1f}s, over the {seconds}s allowed: speed it up, or raise the"
            " limit in check.yml, saying why (see AGENTS.md)"
        )
        sys.exit(1)


COMMANDS: dict[str, Callable[[list[str]], None]] = {"within": within}
"""dev.py's extra commands, by name."""
