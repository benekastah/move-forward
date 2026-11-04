"""The server's token: a secret in a file only my account can read.

Anyone on the machine can connect to the server's port; the token is what tells my requests
from theirs. Browsers get it as a cookie from `/login?token=…`, which `mfd open` visits;
scripts send it as `Authorization: Bearer <token>`.
"""

import hmac
import logging
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

from .private import is_private, make_private

log = logging.getLogger(__name__)

TOKEN_FILE = "token"
"""The token's file, next to the database."""
TOKEN_BYTES = 32
COOKIE = "move_forward_token"
COOKIE_MAX_AGE_SECONDS = 400 * 24 * 3600
"""As long as browsers keep a cookie (400 days), so a browser restart doesn't sign me out."""
BEARER = "Bearer "
NOTICE_COOKIE = "move_forward_notice"
NOTICE_MAX_AGE_SECONDS = 60
"""Long enough for the redirect after an action; a notice not shown by then is stale."""


def token_path(db_path: Path) -> Path:
    return db_path.parent / TOKEN_FILE


def _draft(path: Path) -> Path:
    """A new token in a private file of its own beside `path`, to put in place. The file is
    made private before the token is written to it."""
    fd, draft = tempfile.mkstemp(dir=path.parent, prefix=f".{TOKEN_FILE}.")
    make_private(Path(draft))
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(TOKEN_BYTES) + "\n")
    return Path(draft)


def load_or_create(path: Path) -> str:
    """The token in `path`, made on first use. The file is mine alone (see `private`); one
    that others could read is made private again, with a warning.

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
    if not is_private(path):
        log.warning("%s was readable by others; making it private", path)
        make_private(path)
    return path.read_text().strip()


def matches(token: str, given: str | None) -> bool:
    """Whether `given` is the token, compared in constant time."""
    return given is not None and hmac.compare_digest(token.encode(), given.encode())


def from_request(authorization: str | None, cookie: str | None) -> str | None:
    """The token a request carries: its `Authorization: Bearer` header, else its cookie."""
    if authorization and authorization.startswith(BEARER):
        return authorization[len(BEARER) :].strip()
    return _cookie(cookie, COOKIE)


def _cookie(cookie: str | None, wanted: str) -> str | None:
    """The value of the cookie named `wanted` in a `Cookie` header, if it has one."""
    for part in (cookie or "").split(";"):
        # Parsed by hand: other apps on localhost share our cookies, and `SimpleCookie`
        # drops the whole header (or raises) over one malformed cookie of theirs.
        name, _, value = part.strip().partition("=")
        if name == wanted:
            return value.strip()
    return None


def set_cookie(token: str, secure: bool = False) -> str:
    """The `Set-Cookie` value signing a browser in: scripts can't read it and other sites'
    requests don't carry it; `secure`, over HTTPS, it's never sent over plain HTTP."""
    cookie = (
        f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={COOKIE_MAX_AGE_SECONDS}"
    )
    return cookie + ("; Secure" if secure else "")


def login_url(port: int, token: str, scheme: str = "http") -> str:
    return f"{scheme}://127.0.0.1:{port}/login?token={token}"


def _sign(token: str, text: str) -> str:
    return hmac.new(token.encode(), text.encode(), "sha256").hexdigest()


def notice_cookie(token: str, notice: str, secure: bool = False) -> str:
    """The `Set-Cookie` value that shows `notice` on the next page, once. It's signed, since
    other apps on localhost (any port) can set our cookies too, and a notice of theirs mustn't
    look like the dashboard's own."""
    value = f"{_sign(token, notice)}.{quote(notice, safe='')}"
    cookie = (
        f"{NOTICE_COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict;"
        f" Max-Age={NOTICE_MAX_AGE_SECONDS}"
    )
    return cookie + ("; Secure" if secure else "")


CLEAR_NOTICE = f"{NOTICE_COOKIE}=; Path=/; Max-Age=0"
"""The `Set-Cookie` value that drops the notice once a page has shown it."""


def notice_from(token: str, cookie: str | None) -> str | None:
    """The notice a request's `Cookie` header carries: "" when it's not signed by `token`,
    None when there's no notice cookie at all."""
    value = _cookie(cookie, NOTICE_COOKIE)
    if not value:
        return None
    signature, _, quoted = value.partition(".")
    notice = unquote(quoted)
    return notice if hmac.compare_digest(_sign(token, notice), signature) else ""
