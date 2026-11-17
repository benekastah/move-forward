"""move-forward's own additions to dev.py (see its docstring): `install` ends by writing a
config with `mfd init` when there's none yet, and the man page has a SYNOPSIS.

Held to dev.py's rules: standard library only, and Python 3.11 syntax.
"""

import os
import sys
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


# As `mfd --help` has it.
MAN_SYNOPSIS = {"mfd": "**mfd** [**-c** *config*] [**-v**] *command* [*args*]"}
