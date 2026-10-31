"""The local HTTP server: server-rendered pages, a few form endpoints, and a background refresh."""

import logging
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import BaseModel, ValidationError

from . import actions, render, shell
from .config import Config
from .db import Database
from .domain import Ping
from .plugin import ActionError
from .refresh import build_dashboard, plugin_page, refresh, run_action
from .repositories import PingRepository

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/logo.svg": ("logo.svg", "image/svg+xml"),
}
"""Served paths to the file in `STATIC_DIR` and its content type."""
MAX_BODY_BYTES = 64 * 1024


class PingForm(BaseModel):
    """Fields of the "mark as sent" form on the ping page."""

    pr_url: str
    target: str
    """`user:<login>` or `team:<slug>`, as the ping targets name them."""
    kind: Literal["first", "follow-up"]
    message: str = ""


class App:
    """Shared state for the request handlers: config, database and the refresh loop."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self._refresh_lock = threading.Lock()
        self._wake = threading.Event()
        self.refreshing = False
        self.last_refresh: float = 0
        """`time.monotonic()` when the last refresh started; 0 before the first."""

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
        if (
            not self.refreshing
            and time.monotonic() - self.last_refresh >= self.config.refresh_on_load_seconds
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

    def run_action(self, name: str, fields: dict[str, str]) -> tuple[str, bool]:
        """Run a button's action and refresh afterwards, so the page reflects it soon.

        Returns the notice for the page and whether the action succeeded.
        """
        try:
            if "/" in name:
                notice = run_action(self.config, self.db, name, fields)
            else:
                notice = actions.run(name, self.config, fields)
        except ActionError as e:
            return f"failed: {e}", False
        self.request_refresh()
        return notice, True

    def plugin_page(self, path: str, query: str) -> str | None:
        """An enabled plugin's page; None when there's none at `path`."""
        return plugin_page(self.config, self.db, path, parse_qs(query, keep_blank_values=True))

    def ping_page(self, pr_url: str) -> str | None:
        return render.ping_page(build_dashboard(self.config, self.db), pr_url)

    def focus_page(
        self, category: str, index: int, notice: str = "", done: tuple[str, str] | None = None
    ) -> str | None:
        """The category list without a category, else that category's item at `index`.

        `done` is the (entry, text) of a todo just ticked off, kept on the page to undo.
        """
        self.refresh_if_stale()
        dashboard = build_dashboard(self.config, self.db)
        if not category:
            return render.focus_categories_page(dashboard)
        done_item = dashboard.done_todo(*done) if done else None
        return render.focus_page(dashboard, category, index, notice, done_item)

    def record_ping(self, form: PingForm) -> None:
        PingRepository(self.db).add(
            Ping(
                pr_url=form.pr_url,
                target=form.target,
                kind=form.kind,
                message=form.message,
                sent_at=datetime.now(UTC),
            )
        )


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
            log.debug(format, *args)

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

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path == "/":
                notice = parse_qs(url.query).get("notice", [""])[0]
                self._html(HTTPStatus.OK, app.dashboard_page(notice, _open_keys(url.query)))
            elif url.path == "/ping":
                pr_url = parse_qs(url.query).get("pr", [""])[0]
                page = app.ping_page(pr_url)
                if page is None:
                    self._html(HTTPStatus.NOT_FOUND, "<p>Not one of my open PRs.</p>")
                else:
                    self._html(HTTPStatus.OK, page)
            elif url.path == "/focus":
                params = parse_qs(url.query, keep_blank_values=True)
                category = params.get("cat", [""])[0]
                notice = params.get("notice", [""])[0]
                done = (
                    (params["done_entry"][0], params["done_text"][0])
                    if "done_text" in params
                    else None
                )
                page = app.focus_page(category, _int(params, "i"), notice, done)
                if page is None:
                    self._html(HTTPStatus.NOT_FOUND, "<p>No such focus category.</p>")
                else:
                    self._html(HTTPStatus.OK, page)
            elif (page := app.plugin_page(url.path, url.query)) is not None:
                self._html(HTTPStatus.OK, page)
            elif url.path in STATIC_FILES:
                name, content_type = STATIC_FILES[url.path]
                self._send(HTTPStatus.OK, (STATIC_DIR / name).read_text(), content_type)
            else:
                self._html(HTTPStatus.NOT_FOUND, "<p>Not found.</p>")

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(min(length, MAX_BODY_BYTES))

        def do_POST(self) -> None:
            url = urlsplit(self.path)
            if url.path == "/refresh":
                app.request_refresh()
                self._redirect(_back_to_page(url.query))
            elif url.path == "/pings":
                try:
                    form = PingForm.model_validate(_form_fields(self._read_body()))
                except ValidationError as e:
                    self._html(HTTPStatus.BAD_REQUEST, f"<pre>{e}</pre>")
                    return
                app.record_ping(form)
                self._redirect("/")
            elif url.path.startswith("/actions/"):
                name = url.path[len("/actions/") :]
                fields = _form_fields(self._read_body())
                notice, ok = app.run_action(name, fields)
                extra = actions.redirect_params(name, fields) if ok else {}
                self._redirect(_back_to_page(url.query, notice, extra))
            else:
                self._html(HTTPStatus.NOT_FOUND, "<p>Not found.</p>")

    return Handler


def serve(config: Config, db: Database, port: int | None = None) -> None:
    """Serve forever on localhost, refreshing in the background."""
    app = App(config, db)
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
