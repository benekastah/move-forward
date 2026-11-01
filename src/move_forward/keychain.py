"""Secrets in the system's own store, for tokens that aren't in an environment variable:
the macOS keychain, Windows' Credential Manager, or the desktop keyring on Linux (through
`secret-tool`, where it's installed).

Each is keyed by a service (e.g. `move-forward-jira`) and an account (my email).
`mfd token set` stores a plugin's token with `write`; the plugin reads it with `token`.
"""

import ctypes
import os
import sys
from typing import Protocol, runtime_checkable

from .shell import CommandError, run


@runtime_checkable
class TokenSettings(Protocol):
    """A plugin config section naming where its token is: an environment variable, else
    the secret store under a service. `mfd token` works for any plugin whose section has
    both."""

    token_env: str
    keychain_service: str


def token(settings: TokenSettings, account: str) -> str | None:
    """The token from `settings.token_env`, else from the secret store; None when neither
    has one."""
    return os.environ.get(settings.token_env) or read(settings.keychain_service, account)


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


def write(service: str, account: str, secret: str) -> None:
    """Store `secret` for `service` and `account`, replacing what's there. The secret goes
    to the store on standard input (or, on Windows, through Credential Manager's own
    CredWriteW), never on a command line, where other processes could see it.

    Raises ValueError for an empty secret, or a secret, service or account with a quote,
    backslash or line break (no token has one, and `security` couldn't take it);
    CommandError when the store refuses it, or doesn't give it back afterwards; and
    FileNotFoundError when Linux has no `secret-tool`.
    """
    if not secret:
        raise ValueError("a token can't be empty")
    for what, value in (("token", secret), ("service", service), ("account", account)):
        if any(c in value for c in '"\\\r\n'):
            raise ValueError(f"a {what} can't hold quotes, backslashes or line breaks")
    if sys.platform == "darwin":
        # `security -i` reads its commands from stdin, so the secret stays off the command
        # line; -U replaces an item that's there.
        line = f'add-generic-password -U -s "{service}" -a "{account}" -w "{secret}"\n'
        run(["security", "-i"], input=line)
    elif sys.platform == "win32":
        _credential_manager_write(service, account, secret)
    else:
        run(
            ["secret-tool", "store", f"--label={service}", "service", service, "account", account],
            input=secret,
        )
    # `security -i` can exit 0 when the command it read failed; check it took.
    if read(service, account) != secret:
        raise CommandError(f"the secret store didn't keep the token for {service}")


def how_to_store(service: str, account: str) -> str:
    """The command that stores a secret where `read` finds it, on this system."""
    if sys.platform == "darwin":
        return f"security add-generic-password -s {service} -a {account} -w <token>"
    if sys.platform == "win32":
        return f"cmdkey /generic:{service} /user:{account} /pass:<token>"
    return f"secret-tool store --label={service} service {service} account {account}"


if sys.platform == "win32":
    from ctypes import wintypes

    class _Credential(ctypes.Structure):
        """Windows' CREDENTIALW."""

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


_GENERIC = 1  # CRED_TYPE_GENERIC: what `cmdkey /generic` stores
_PERSIST_LOCAL_MACHINE = 2  # kept across sign-ins, on this machine only


def _credential_manager(target: str) -> str | None:
    """A generic credential's secret from Windows' Credential Manager, read with Windows'
    own CredReadW."""
    if sys.platform != "win32":
        return None
    advapi32 = ctypes.WinDLL("advapi32")
    found = ctypes.POINTER(_Credential)()
    if not advapi32.CredReadW(target, _GENERIC, 0, ctypes.byref(found)):
        return None
    try:
        size = found.contents.CredentialBlobSize
        blob = bytes(found.contents.CredentialBlob[:size])
    finally:
        advapi32.CredFree(found)
    # cmdkey and `write` store the secret as UTF-16; other tools may store UTF-8.
    try:
        return blob.decode("utf-16-le") if size % 2 == 0 else blob.decode()
    except UnicodeDecodeError:
        return blob.decode(errors="replace")


def _credential_manager_write(target: str, account: str, secret: str) -> None:
    """Store a generic credential in Windows' Credential Manager with CredWriteW, as
    `cmdkey /generic` would."""
    if sys.platform != "win32":
        return
    blob = secret.encode("utf-16-le")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = _Credential(
        Type=_GENERIC,
        TargetName=target,
        UserName=account,
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=_PERSIST_LOCAL_MACHINE,
    )
    if not ctypes.WinDLL("advapi32", use_last_error=True).CredWriteW(ctypes.byref(credential), 0):
        raise CommandError(f"CredWriteW: {ctypes.FormatError(ctypes.get_last_error())}")
