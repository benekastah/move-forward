"""The server's token: a secret in a file only my account can read.

Anyone on the machine can connect to the server's port; the token is what tells my requests
from theirs. Scripts send it as `Authorization: Bearer <token>`. Browsers don't hold it: a
visit to `/login`, which `mfd open` makes, starts a session, and the browser's cookie names
that session (see `repositories.SessionRepository`), which can expire or be revoked.

The token never goes in a URL or to a server that hasn't proved it has it. `mfd open` runs a
handshake first (see `Handshake`): each side proves it knows the token by an HMAC over fresh
nonces, so a program squatting on the port can't learn it, and the browser is sent to
`/login?code=…`, a code good once, for a minute.

Browsers reach the dashboard under `HOST`, not its address. Cookies ignore ports, so a
cookie for `127.0.0.1` would go to every other program on the machine that listens on that
address, and one for `localhost` to every other `localhost` page; `mfd.localhost` (browsers
send every `*.localhost` name to this machine) is a name nothing else uses.
"""

import hmac
import logging
import os
import secrets
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, unquote

from .private import is_private, make_private

log = logging.getLogger(__name__)

TOKEN_FILE = "token"
"""The token's file, next to the database."""
TOKEN_BYTES = 32
HOST = "mfd.localhost"
"""The name browsers use for the dashboard."""
SESSION_COOKIE = "move_forward_session"
SESSION_DAYS = 400
COOKIE_MAX_AGE_SECONDS = SESSION_DAYS * 24 * 3600
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
    with os.fdopen(fd, "w", encoding="utf-8") as f:
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
    if not path.read_text(
        encoding="utf-8"
    ).strip():  # left empty by a crash: replace it, in one step
        os.replace(_draft(path), path)
    if not is_private(path):
        log.warning("%s was readable by others; making it private", path)
        make_private(path)
    return path.read_text(encoding="utf-8").strip()


def matches(token: str, given: str | None) -> bool:
    """Whether `given` is the token, compared in constant time."""
    return given is not None and hmac.compare_digest(token.encode(), given.encode())


def bearer(authorization: str | None) -> str | None:
    """The token in an `Authorization: Bearer` header, if it has one."""
    if authorization and authorization.startswith(BEARER):
        return authorization[len(BEARER) :].strip()
    return None


def session_cookie(port: int) -> str:
    """The session cookie's name. It has the port in it, since cookies ignore ports: two
    dashboards on this machine (a second, for development) would otherwise sign each other
    out."""
    return f"{SESSION_COOKIE}_{port}"


def session_from(cookie: str | None, port: int) -> str | None:
    """The session id a `Cookie` header carries for the dashboard on `port`."""
    return _cookie(cookie, session_cookie(port))


def _cookie(cookie: str | None, wanted: str) -> str | None:
    """The value of the cookie named `wanted` in a `Cookie` header, if it has one."""
    for part in (cookie or "").split(";"):
        # Parsed by hand: other apps on localhost share our cookies, and `SimpleCookie`
        # drops the whole header (or raises) over one malformed cookie of theirs.
        name, _, value = part.strip().partition("=")
        if name == wanted:
            return value.strip()
    return None


def set_cookie(session_id: str, port: int) -> str:
    """The `Set-Cookie` value signing a browser in: scripts can't read it and other sites'
    requests don't carry it. It has no `Domain`, so only the host that set it gets it back."""
    return (
        f"{session_cookie(port)}={session_id}; Path=/; HttpOnly; SameSite=Strict;"
        f" Max-Age={COOKIE_MAX_AGE_SECONDS}"
    )


def origin(port: int) -> str:
    """Where browsers reach the dashboard."""
    return f"http://{HOST}:{port}"


def login_url(port: int, code: str) -> str:
    """Where `mfd open` sends the browser: `/login` with a one-time `code` (see `Handshake`)."""
    return f"{origin(port)}/login?code={code}"


def proof(token: str, role: str, *parts: str) -> str:
    """An HMAC with the token over `role` and `parts`: what each side of the handshake sends
    to show it knows the token, without sending it."""
    return hmac.new(token.encode(), ":".join([role, *parts]).encode(), "sha256").hexdigest()


NONCE_SECONDS = 30
CODE_SECONDS = 60
HANDSHAKE_LIMIT = 64
"""Most nonces (and codes) kept at once, so a stream of unauthenticated requests for them
can't grow the server's memory."""


class Handshake:
    """The nonces and codes `mfd open` signs a browser in with: random, single use, short-lived
    and kept in memory only (a restart forgets them, which only costs a retry).

    `mfd open` asks for a nonce, answers with `proof(token, "client", nonce, own_nonce)`, and
    gets a code with the server's `proof(token, "server", own_nonce, code)`. Only the server
    that knows the token can give the second, so the client checks it before sending the
    browser anywhere, and only a client that knows the token gets a code.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._nonces: dict[str, float] = {}
        self._codes: dict[str, float] = {}

    def _issue(self, kept: dict[str, float], seconds: int) -> str | None:
        with self._lock:
            now = self._clock()
            for stale in [k for k, until in kept.items() if until <= now]:
                del kept[stale]
            if len(kept) >= HANDSHAKE_LIMIT:
                return None
            value = secrets.token_urlsafe(TOKEN_BYTES)
            kept[value] = now + seconds
            return value

    def _take(self, kept: dict[str, float], value: str) -> bool:
        with self._lock:
            until = kept.pop(value, None)
            return until is not None and until > self._clock()

    def nonce(self) -> str | None:
        """A fresh nonce for a client to answer; None when too many are waiting."""
        return self._issue(self._nonces, NONCE_SECONDS)

    def take_nonce(self, nonce: str) -> bool:
        """Use up `nonce`: whether it was issued, unused and still fresh."""
        return self._take(self._nonces, nonce)

    def code(self) -> str | None:
        """A fresh login code; None when too many are waiting."""
        return self._issue(self._codes, CODE_SECONDS)

    def take_code(self, code: str) -> bool:
        """Use up `code`: whether it was issued, unused and still fresh."""
        return self._take(self._codes, code)


def rotate(path: Path) -> str:
    """Replace the token in `path` with a new one, in one step; the new token."""
    os.replace(_draft(path), path)
    return path.read_text(encoding="utf-8").strip()


class TokenFile:
    """The token, read again whenever its file changes, so `mfd token rotate` takes effect in
    a running server."""

    def __init__(self, path: Path):
        self._path = path
        self._stamp: tuple[int, int] | None = None
        self._token = ""

    def get(self) -> str:
        stat = self._path.stat()
        stamp = (stat.st_mtime_ns, stat.st_ino)  # a replaced file has another inode
        if stamp != self._stamp:
            self._token = self._path.read_text(encoding="utf-8").strip()
            self._stamp = stamp
        return self._token


def _sign(token: str, text: str) -> str:
    return hmac.new(token.encode(), text.encode(), "sha256").hexdigest()


def notice_cookie(token: str, notice: str) -> str:
    """The `Set-Cookie` value that shows `notice` on the next page, once. It's signed, since
    other apps on localhost (any port) can set our cookies too, and a notice of theirs mustn't
    look like the dashboard's own."""
    value = f"{_sign(token, notice)}.{quote(notice, safe='')}"
    return (
        f"{NOTICE_COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict;"
        f" Max-Age={NOTICE_MAX_AGE_SECONDS}"
    )


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
    # As bytes: compare_digest refuses a str that isn't ASCII, and any page on another port of
    # this host can set the cookie to one.
    signed = hmac.compare_digest(_sign(token, notice).encode(), signature.encode())
    return notice if signed else ""
