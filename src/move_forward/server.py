"""The local HTTP server: server-rendered pages, a few form endpoints, and a background refresh."""

import logging
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit

from . import auth, render, shell
from .config import Config
from .db import Database
from .plugin import KEPT, ActionError
from .refresh import build_dashboard, kept_item, perform_action, plugin_page, refresh

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/logo.svg": ("logo.svg", "image/svg+xml"),
}
"""Served paths to the file in `STATIC_DIR` and its content type."""
MAX_BODY_BYTES = 64 * 1024
LOGIN_PATH = "/login"
NOT_SIGNED_IN = "<p>Not signed in: run <code>mfd open</code> to open the dashboard signed in.</p>"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
"""Names the page is served under. A request naming any other host is refused, which stops a
web page from reaching the server through a DNS name it controls (DNS rebinding)."""


def _allowed_hosts(port: int) -> set[str]:
    return {f"{host}:{port}" for host in LOCAL_HOSTS}


def _from_this_dashboard(host: str | None, origin: str | None, port: int) -> bool:
    """Whether a request may be served: it names this server as its `Host`, and a browser
    sending it from another page (its `Origin`) isn't sending it from someone else's site.

    Browsers send `Origin` with every POST, so a form on another site can't use the
    dashboard's buttons. Requests without one (curl, scripts) are allowed.
    """
    allowed = _allowed_hosts(port)
    if host not in allowed:
        return False
    return origin is None or origin in {f"http://{h}" for h in allowed}


class App:
    """Shared state for the request handlers: config, database and the refresh loop."""

    def __init__(self, config: Config, db: Database, token: str):
        self.config = config
        self.db = db
        self.token = token
        """What every request but `/login` and the static files must carry; see `auth`."""
        self._refresh_lock = threading.Lock()
        self._wake = threading.Event()
        self.refreshing = False
        self.last_refresh: float | None = None
        """`time.monotonic()` when the last refresh started; None before the first."""

    def refresh_now(self) -> bool:
        """Refresh unless one is already running; returns whether this call did it."""
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            self.refreshing = True
            self.last_refresh = time.monotonic()
            refresh(self.config, self.db)
        finally:
            self.refreshing = False
            self._refresh_lock.release()
        return True

    def request_refresh(self) -> None:
        """Ask the background loop to refresh as soon as it can."""
        self._wake.set()

    def refresh_if_stale(self) -> None:
        """Start a background refresh if the last one began over `refresh_on_load_seconds` ago."""
        if self.refreshing:
            return
        if (
            self.last_refresh is None
            or time.monotonic() - self.last_refresh >= self.config.refresh_on_load_seconds
        ):
            self.request_refresh()

    def run_refresh_loop(self) -> None:
        while True:
            try:
                self.refresh_now()
            except Exception:
                log.exception("refresh failed")
            self._wake.wait(self.config.refresh_seconds)
            self._wake.clear()

    def dashboard_page(self, notice: str = "", open_keys: set[str] | None = None) -> str:
        """The page from stored data; a page load also refreshes stale data in the background."""
        self.refresh_if_stale()
        return render.dashboard_page(
            build_dashboard(self.config, self.db), self.refreshing, notice, open_keys
        )

    def run_action(self, name: str, fields: dict[str, str]) -> tuple[str, dict[str, str]]:
        """Run a button's action and refresh afterwards, so the page reflects it soon.

        Returns the notice for the page and, when it succeeded, its redirect parameters.
        """
        try:
            result = perform_action(self.config, self.db, name, fields)
        except ActionError as e:
            return f"failed: {e}", {}
        self.request_refresh()
        return result.notice, result.params

    def plugin_page(self, path: str, query: str) -> str | None:
        """An enabled plugin's page; None when there's none at `path`."""
        return plugin_page(self.config, self.db, path, parse_qs(query, keep_blank_values=True))

    def focus_page(
        self,
        category: str,
        index: int,
        notice: str = "",
        kept: str = "",
    ) -> str | None:
        """The category list without a category, else that category's item at `index`.

        `kept` names an item an action asked to keep on the page, e.g. a todo just ticked off,
        so it can be undone; see `Plugin.kept_item`.
        """
        self.refresh_if_stale()
        dashboard = build_dashboard(self.config, self.db)
        if not category:
            return render.focus_categories_page(dashboard)
        done_item = kept_item(self.config, self.db, kept) if kept else None
        return render.focus_page(dashboard, category, index, notice, done_item)


def _open_keys(query: str) -> set[str] | None:
    """The `open` query parameter as a set of section keys; None when it's absent."""
    values = parse_qs(query, keep_blank_values=True).get("open")
    if values is None:
        return None
    return {key for key in values[0].split(",") if key}


def _back_to_page(query: str, notice: str = "", extra: dict[str, str] | None = None) -> str:
    """Where a POST redirects: the request's `back` path (a focus page) if it's local, else the
    dashboard, keeping the request's `open` parameter. The notice and `extra` parameters ride
    along either way."""
    params: dict[str, str] = dict(extra or {})
    if notice:
        params["notice"] = notice
    query_params = parse_qs(query, keep_blank_values=True)
    back = query_params.get("back", [""])[0]
    if back.startswith("/") and not back.startswith("//"):
        return back + ("&" if "?" in back else "?") + urlencode(params) if params else back
    values = query_params.get("open")
    if values is not None:
        params["open"] = values[0]
    return "/?" + urlencode(params) if params else "/"


TOKEN_PARAM_RE = re.compile(r"([?&]token=)[^&\s]*")


def _redact(text: str) -> str:
    """`text` without the value of a `token` query parameter, for the log."""
    return TOKEN_PARAM_RE.sub(r"\1…", text)


def _int(query_params: dict[str, list[str]], name: str) -> int:
    try:
        return int(query_params.get(name, ["0"])[0])
    except ValueError:
        return 0


def _form_fields(body: bytes) -> dict[str, str]:
    """The first value of each form field."""
    return {k: v[0] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            log.debug(format, *(_redact(str(a)) for a in args))

        def _send(self, status: HTTPStatus, body: str, content_type: str) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: HTTPStatus, body: str) -> None:
            self._send(status, body, "text/html; charset=utf-8")

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _refuse_other_sites(self, check_origin: bool) -> bool:
            """Answer 403 to a request from another site; returns whether it did."""
            origin = self.headers.get("Origin") if check_origin else None
            port = cast(HTTPServer, self.server).server_port
            if _from_this_dashboard(self.headers.get("Host"), origin, port):
                return False
            self._html(HTTPStatus.FORBIDDEN, "<p>Forbidden: not from this dashboard.</p>")
            return True

        def _refuse_strangers(self) -> bool:
            """Answer 401 to a request without the token; returns whether it did."""
            given = auth.from_request(self.headers.get("Authorization"), self.headers.get("Cookie"))
            if auth.matches(app.token, given):
                return False
            self._html(HTTPStatus.UNAUTHORIZED, NOT_SIGNED_IN)
            return True

        def _login(self, query: str) -> None:
            """Sign the browser in with the token from `mfd open`, then go to the dashboard, so
            the token doesn't stay in the address bar."""
            given = parse_qs(query).get("token", [None])[0]
            if not auth.matches(app.token, given):
                self._html(HTTPStatus.UNAUTHORIZED, NOT_SIGNED_IN)
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", auth.set_cookie(app.token))
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if self._refuse_other_sites(check_origin=False):
                return
            url = urlsplit(self.path)
            if url.path in STATIC_FILES:
                name, content_type = STATIC_FILES[url.path]
                self._send(HTTPStatus.OK, (STATIC_DIR / name).read_text(), content_type)
                return
            if url.path == LOGIN_PATH:
                self._login(url.query)
                return
            if self._refuse_strangers():
                return
            if url.path == "/":
                notice = parse_qs(url.query).get("notice", [""])[0]
                self._html(HTTPStatus.OK, app.dashboard_page(notice, _open_keys(url.query)))
            elif url.path == "/focus":
                params = parse_qs(url.query, keep_blank_values=True)
                category = params.get("cat", [""])[0]
                notice = params.get("notice", [""])[0]
                kept = params.get(KEPT, [""])[0]
                page = app.focus_page(category, _int(params, "i"), notice, kept)
                if page is None:
                    self._html(HTTPStatus.NOT_FOUND, "<p>No such focus category.</p>")
                else:
                    self._html(HTTPStatus.OK, page)
            elif (page := app.plugin_page(url.path, url.query)) is not None:
                self._html(HTTPStatus.OK, page)
            else:
                self._html(HTTPStatus.NOT_FOUND, "<p>Not found.</p>")

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(min(length, MAX_BODY_BYTES))

        def do_POST(self) -> None:
            if self._refuse_other_sites(check_origin=True) or self._refuse_strangers():
                return
            url = urlsplit(self.path)
            if url.path == "/refresh":
                app.request_refresh()
                self._redirect(_back_to_page(url.query))
            elif url.path.startswith("/actions/"):
                name = url.path[len("/actions/") :]
                fields = _form_fields(self._read_body())
                notice, extra = app.run_action(name, fields)
                self._redirect(_back_to_page(url.query, notice, extra))
            else:
                self._html(HTTPStatus.NOT_FOUND, "<p>Not found.</p>")

    return Handler


def serve(config: Config, db: Database, token: str, port: int | None = None) -> None:
    """Serve forever on localhost, refreshing in the background."""
    app = App(config, db, token)
    threading.Thread(target=app.run_refresh_loop, name="refresh", daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port or config.port), make_handler(app))
    log.info("serving on http://127.0.0.1:%d", server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # The interpreter waits for a refresh in flight; `shell.stop` makes it wind down fast.
        log.info("shutting down on ctrl-c…")
        shell.stop()
    finally:
        server.server_close()
