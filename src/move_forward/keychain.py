"""Secrets in the system's own store, for tokens that aren't in an environment variable:
the macOS keychain, Windows' Credential Manager, or the desktop keyring on Linux (through
`secret-tool`, where it's installed).

Each is keyed by a service (e.g. `move-forward-jira`) and an account (my email).
"""

import ctypes
import sys

from .shell import CommandError, run


def read(service: str, account: str) -> str | None:
    """The secret stored for `service` and `account`, or None when there's none (or no
    store this system has)."""
    if sys.platform == "darwin":
        command = ["security", "find-generic-password", "-s", service, "-a", account, "-w"]
    elif sys.platform == "win32":
        return _credential_manager(service)
    else:
        command = ["secret-tool", "lookup", "service", service, "account", account]
    try:
        return run(command).strip() or None
    except (CommandError, FileNotFoundError):
        return None


def how_to_store(service: str, account: str) -> str:
    """The command that stores a secret where `read` finds it, on this system."""
    if sys.platform == "darwin":
        return f"security add-generic-password -s {service} -a {account} -w <token>"
    if sys.platform == "win32":
        return f"cmdkey /generic:{service} /user:{account} /pass:<token>"
    return f"secret-tool store --label={service} service {service} account {account}"


def _credential_manager(target: str) -> str | None:
    """A generic credential's secret from Windows' Credential Manager (`cmdkey /generic`
    stores them), read with Windows' own CredReadW."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    generic = 1  # CRED_TYPE_GENERIC
    advapi32 = ctypes.WinDLL("advapi32")
    found = ctypes.POINTER(Credential)()
    if not advapi32.CredReadW(target, generic, 0, ctypes.byref(found)):
        return None
    try:
        size = found.contents.CredentialBlobSize
        blob = bytes(found.contents.CredentialBlob[:size])
    finally:
        advapi32.CredFree(found)
    # cmdkey stores the password as UTF-16; other tools may store UTF-8.
    try:
        return blob.decode("utf-16-le") if size % 2 == 0 else blob.decode()
    except UnicodeDecodeError:
        return blob.decode(errors="replace")
