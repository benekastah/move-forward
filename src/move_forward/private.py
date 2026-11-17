"""Files only I can read: the sign-in token, the database and its backups, the plugins' data
directories.

On macOS and Linux that's the file's mode: 0600, or 0700 for a directory. Windows has no
modes (`chmod` there only sets read-only), so there it's the file's access list, set with
the built-in `icacls`: the rules inherited from the folder are removed and only I am
granted access. Under my user profile the inherited rules already keep other users out,
but `db_path` can point anywhere.
"""

import ctypes
import getpass
import os
import stat
import sys
from pathlib import Path

from move_forward.shell import run

WINDOWS = sys.platform == "win32"
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
OWNER_RIGHTS_SID = "S-1-3-4"
SYSTEM_HOLDERS = {
    SYSTEM_SID: "nt authority\\system",
    ADMINISTRATORS_SID: "builtin\\administrators",
    OWNER_RIGHTS_SID: "owner rights",
}
"""Who may hold access on Windows besides me, by well-known SID, with their English names: the
system itself, administrators (who can take any file anyway) and the file's owner (me). Python
grants them when it makes a private file; what matters is that no other user, nor the Users
group, is listed. icacls names them in Windows' language, so they're looked up by SID, and
the English name is used only if that fails."""


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
    run(["icacls", str(path), "/inheritance:r", "/grant:r", grant], encoding="oem")


def is_private(path: Path) -> bool:
    """Whether only I can read `path`: no group or other mode bits, or on Windows an access
    list naming no other user (see `SYSTEM_HOLDERS`), in whatever language Windows names them."""
    if not WINDOWS:
        return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    holders = set(_holders(run(["icacls", str(path)], encoding="oem"), str(path)))
    system = {_account(sid) or english for sid, english in SYSTEM_HOLDERS.items()}
    return holders <= {_me().lower(), *system}


def _account(sid: str) -> str | None:
    """The account with the string `sid`, as `icacls` names it, lower-cased: `domain\\name`, or
    just its name when it has no domain (OWNER RIGHTS). None if it can't be looked up."""
    found = _lookup(sid)
    if found is None:
        return None
    domain, name = found
    return f"{domain}\\{name}".lower() if domain else name.lower()


def _lookup(sid: str) -> tuple[str, str] | None:
    """The domain and name of the account with the string `sid`, in Windows' language, from
    Windows' own LookupAccountSidW; None elsewhere, or if Windows can't say."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    binary = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(sid, ctypes.byref(binary)):
        return None
    try:
        name, domain = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        name_size, domain_size = wintypes.DWORD(len(name)), wintypes.DWORD(len(domain))
        use = wintypes.DWORD()
        found = advapi32.LookupAccountSidW(
            None,
            binary,
            name,
            ctypes.byref(name_size),
            domain,
            ctypes.byref(domain_size),
            ctypes.byref(use),
        )
    finally:
        ctypes.WinDLL("kernel32").LocalFree(binary)
    return (domain.value, name.value) if found else None


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
