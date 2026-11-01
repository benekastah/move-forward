"""Files only I can read: the sign-in token, the database and its backups, the certificates'
keys.

On macOS and Linux that's the file's mode: 0600, or 0700 for a directory. Windows has no
modes (`chmod` there only sets read-only), so there it's the file's access list, set with
the built-in `icacls`: the rules inherited from the folder are removed and only I am
granted access. Under my user profile the inherited rules already keep other users out,
but `db_path` can point anywhere.
"""

import getpass
import os
import stat
import sys
from pathlib import Path

from .shell import run

WINDOWS = sys.platform == "win32"
SYSTEM_HOLDERS = {"nt authority\\system", "builtin\\administrators", "owner rights"}
"""Who may hold access on Windows besides me: the system itself, administrators (who can
take any file anyway) and the file's owner (me). Python grants them when it makes a private
file; what matters is that no other user, nor the Users group, is listed."""


def _me() -> str:
    """My account as `icacls` names it: `DOMAIN\\user` (the domain is the machine's name for
    a local account)."""
    domain = os.environ.get("USERDOMAIN")
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def make_private(path: Path) -> None:
    """Let only me read and write `path` (a file or a directory)."""
    if not WINDOWS:
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    grant = f"{_me()}:(OI)(CI)F" if path.is_dir() else f"{_me()}:F"
    run(["icacls", str(path), "/inheritance:r", "/grant:r", grant])


def is_private(path: Path) -> bool:
    """Whether only I can read `path`: no group or other mode bits, or on Windows an access
    list naming no other user (see `SYSTEM_HOLDERS`)."""
    if not WINDOWS:
        return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    holders = set(_holders(run(["icacls", str(path)]), str(path)))
    return holders <= {_me().lower(), *SYSTEM_HOLDERS}


def _holders(listing: str, path: str) -> list[str]:
    """Who `icacls`' listing of `path` grants access to, lower-cased. It's the path and the
    first entry on one line, an entry a line after that, then a blank line and a summary."""
    holders: list[str] = []
    for n, line in enumerate(listing.splitlines()):
        entry = line[len(path) :] if n == 0 else line
        entry = entry.strip()
        if not entry:
            break
        holders.append(entry.split(":(", 1)[0].lower())
    return holders
