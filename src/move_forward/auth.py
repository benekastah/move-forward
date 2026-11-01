"""The server's token: a secret in a file only my account can read.

Anyone on the machine can connect to the server's port; the token is what tells my requests
from theirs. Browsers get it as a cookie from `/login?token=…`, which `mfd open` visits;
scripts send it as `Authorization: Bearer <token>`.
"""

import hmac
import logging
import os
import secrets
import stat
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

TOKEN_FILE = "token"
"""The token's file, next to the database."""
TOKEN_BYTES = 32
COOKIE = "move_forward_token"
COOKIE_MAX_AGE_SECONDS = 400 * 24 * 3600
"""As long as browsers keep a cookie (400 days), so a browser restart doesn't sign me out."""
BEARER = "Bearer "


def token_path(db_path: Path) -> Path:
    return db_path.parent / TOKEN_FILE


def _draft(path: Path) -> Path:
    """A new token in a private file of its own beside `path` (mode 0600), to put in place."""
    fd, draft = tempfile.mkstemp(dir=path.parent, prefix=f".{TOKEN_FILE}.")
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(TOKEN_BYTES) + "\n")
    return Path(draft)


def load_or_create(path: Path) -> str:
    """The token in `path`, made on first use. The file is mine alone (mode 0600); one that
    others could read is made private again, with a warning.

    A new token is written to a private file of its own first and then linked into place,
    so a process starting at the same moment never reads it half written: it either finds
    the whole token or makes the file itself.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        draft = _draft(path)
        try:
            os.link(draft, path)  # fails if another process got there first; theirs stands
        except FileExistsError:
            pass
        finally:
            os.unlink(draft)
    if not path.read_text().strip():  # left empty by a crash: replace it, in one step
        os.replace(_draft(path), path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning("%s was readable by others (mode %o); making it private", path, mode)
        path.chmod(0o600)
    return path.read_text().strip()


def matches(token: str, given: str | None) -> bool:
    """Whether `given` is the token, compared in constant time."""
    return given is not None and hmac.compare_digest(token.encode(), given.encode())


def from_request(authorization: str | None, cookie: str | None) -> str | None:
    """The token a request carries: its `Authorization: Bearer` header, else its cookie."""
    if authorization and authorization.startswith(BEARER):
        return authorization[len(BEARER) :].strip()
    for part in (cookie or "").split(";"):
        # Parsed by hand: other apps on localhost share our cookies, and `SimpleCookie`
        # drops the whole header (or raises) over one malformed cookie of theirs.
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value.strip()
    return None


def set_cookie(token: str) -> str:
    """The `Set-Cookie` value signing a browser in: scripts can't read it and other sites'
    requests don't carry it."""
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={COOKIE_MAX_AGE_SECONDS}"


def login_url(port: int, token: str) -> str:
    return f"http://127.0.0.1:{port}/login?token={token}"
