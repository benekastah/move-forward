"""The local HTTP server: server-rendered pages, a few form endpoints, and a background refresh."""

import json
import logging
import re
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import Field, TypeAdapter, ValidationError

from . import act_now, auth, render, settings, shell, tag_view
from .change_checks import ChangeChecks
from .config import Config
from .db import Database
from .graph import Graph, Ref
from .notifications import NotificationRepository
from .panels import Layout
from .plugin import KEPT, MAX_SUBJECTS, ActionError, Response
from .refresh import (
    PageFailed,
    build_dashboard,
    kept_item,
    perform_action,
    plugin_page,
    refresh,
    refresh_subjects,
    stored_graph,
    to_collect,
)
from .registry import Enabled, enabled
from .repositories import LayoutRepository, SessionRepository

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon-refresh.svg": ("favicon-refresh.svg", "image/svg+xml"),
    "/favicon-refresh-1.svg": ("favicon-refresh-1.svg", "image/svg+xml"),
    "/favicon-refresh-2.svg": ("favicon-refresh-2.svg", "image/svg+xml"),
    "/logo.svg": ("logo.svg", "image/svg+xml"),
}
"""Served paths to the file in `STATIC_DIR` and its content type."""
MAX_BODY_BYTES = 64 * 1024
FRAME_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
}
"""Sent with every answer. `frame-ancestors` is the modern form; `X-Frame-Options` covers older
browsers. A fuller policy (`script-src`) would have to allow the pages' inline scripts, which
need a nonce per page: not worth the risk of breaking them here."""
REQUEST_TIMEOUT_SECONDS = 30
"""How long a connection can sit silent while a request is read before it is dropped, so a
client that opens one and sends a byte at a time can't hold a thread for ever. Regular
requests close their connection (HTTP/1.0); the notification stream keeps writing."""
NOTIFICATION_STREAM_SECONDS = 5
"""How often a connected dashboard tab checks for notifications and gets a heartbeat."""
CHANGE_LOOP_SECONDS = 1.0
"""How often the change loop looks for plugins whose `change_check_seconds` have passed."""
LOGIN_PATH = "/login"
LOGIN_CHALLENGE_PATH = "/login/challenge"
LOGIN_CODE_PATH = "/login/code"
HANDSHAKE_PATHS = (LOGIN_CHALLENGE_PATH, LOGIN_CODE_PATH)
"""`mfd open`'s steps; see `auth.Handshake`."""
NOTIFICATIONS_PATH = "/notifications"
NOTIFICATIONS_STREAM_PATH = "/notifications/stream"
SUBJECTS_PATH = "/refresh/subjects"
_SUBJECTS = TypeAdapter(Annotated[list[Ref], Field(max_length=MAX_SUBJECTS)])
"""`POST /refresh/subjects`'s body: the entities to fetch again, as `[{kind, id}]`."""
RESERVED_PATHS = frozenset(
    {
        "focus",
        "settings",
        "login",
        "notifications",
        "refresh",
        "layout",
        "actions",
        *(path.lstrip("/") for path in STATIC_FILES),
    }
)
"""The first segments of core's own paths: no plugin may be named one, since its pages are
served at `/<plugin>/…`."""
NOT_SIGNED_IN = "<p>Not signed in: run <code>mfd open</code> to open the dashboard signed in.</p>"
LOCAL_HOSTS = (auth.HOST, "127.0.0.1", "localhost")
"""Names the server answers to. A request naming any other host is refused, which stops a web
page from reaching the server through a DNS name it controls (DNS rebinding). Browsers are
sent on to `auth.HOST` (see `do_GET`); the others are for scripts with the token."""


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

    def __init__(
        self,
        config: Config,
        db: Database,
        token: str,
        config_path: Path | None = None,
        token_file: auth.TokenFile | None = None,
    ):
        self.config = config
        self.config_path = config_path
        """The file `config` was read from, which the settings page saves into; None when
        there's none (tests), and no saving."""
        self.db = db
        self._token = token
        self._token_file = token_file
        """Where the token is read again from, when it is replaced (`mfd token rotate`)."""
        self.handshake = auth.Handshake()
        """The nonces and codes `mfd open` signs a browser in with (see `auth.Handshake`)."""
        self._refresh_lock = threading.Lock()
        self._wake = threading.Event()
        self.refreshing: frozenset[str] = frozenset()
        """The plugins the refresh running now collects; empty when none is running."""
        self.last_refreshed: dict[str, float] = {}
        """`time.monotonic()` when each plugin's last refresh started."""
        self._pending: set[str] | None = set()
        """Plugins asked for since the loop last ran: by the Refresh buttons (None: all of
        them), a change check, or a plugin's button."""
        self._pending_lock = threading.Lock()
        self._subjects: list[Ref] = []
        """Entities asked to be fetched again (`request_refresh_subjects`), under
        `_pending_lock`."""
        self._last_checked: dict[str, float] = {}
        """`time.monotonic()` when each plugin was last asked whether it `changed`."""
        self.checks = ChangeChecks()
        """What moved in each plugin's tool, as its change checks said."""

    @property
    def token(self) -> str:
        """What every request but the sign-in handshake, `/login` and the static files must
        carry (or a session); see `auth`."""
        if self._token_file is not None:
            self._token = self._token_file.get()
        return self._token

    def refreshable(self) -> set[str]:
        """The plugins a refresh can be asked for: those that collect."""
        plugins = enabled(self.config, self.db, datetime.now(UTC))
        first, after = to_collect(plugins)
        return {e.name for e in first + after}

    def intervals(self) -> dict[str, int]:
        """How often each plugin that refreshes on its own does: its `refresh_seconds`."""
        plugins = enabled(self.config, self.db, datetime.now(UTC))
        return {e.name: s for e in plugins if (s := e.refresh_seconds) is not None}

    def due(self, now: float | None = None) -> set[str]:
        """The plugins whose `refresh_seconds` have passed since they last refreshed."""
        now = time.monotonic() if now is None else now
        last = self.last_refreshed
        return {
            name
            for name, seconds in self.intervals().items()
            if name not in last or now - last[name] >= seconds
        }

    def refresh_now(self, only: set[str] | None = None) -> bool:
        """Refresh every plugin, or `only` those, unless a refresh is already running;
        returns whether this call did it."""
        if not self._refresh_lock.acquire(blocking=False):
            return False
        try:
            first, after = to_collect(enabled(self.config, self.db, datetime.now(UTC)), only)
            self.refreshing = frozenset(e.name for e in first + after)
            started = time.monotonic()
            for name in self.intervals() if only is None else only:
                self.last_refreshed[name] = started
            refresh(self.config, self.db, None, only, self.checks)
            self._after_refresh(self.refreshing)
        finally:
            self.refreshing = frozenset()
            self._refresh_lock.release()
        return True

    def _after_refresh(self, names: frozenset[str]) -> None:
        """Start the wait for the next change check over for the plugins just refreshed (each
        was asked as its collect started), and ask the plugins that watch them
        (`Plugin.watches`) at once, refreshing those that did."""
        now = time.monotonic()
        for name in names:
            self._last_checked[name] = now
        plugins = enabled(self.config, self.db, datetime.now(UTC))
        watchers = [
            e for e in plugins if e.name not in names and names.intersection(e.plugin.watches)
        ]
        if not watchers:
            return
        graph, _ = stored_graph(self.db, plugins)
        changed = {e.name: why for e in watchers if (why := self._changed(e, graph)) is not None}
        if changed:
            log.info("changed with %s: %s; refreshing", ", ".join(sorted(names)), _said(changed))
            self.request_refresh(set(changed))

    def _changed(self, e: Enabled, graph: Graph) -> str | None:
        """Why the plugin's change check says something new moved in its tool ("" when it
        doesn't say), kept by `checks`; None when nothing did, or the check fails (raises, or
        answers with something that isn't a `Changed`): logged at debug and tried again next
        time, against the same as this one, so one plugin's check can't stop the others' and
        what it missed isn't lost."""
        try:
            answer = self.checks.check(e, graph)
            return None if answer is None else answer.reason
        except Exception:
            log.debug("%s: change check failed", e.name, exc_info=True)
            return None

    def check_for_changes(self, now: float | None = None) -> set[str]:
        """Ask each plugin whose `change_check_seconds` have passed whether it `changed`,
        except those refreshing now, and ask for a refresh of those that did; returns them."""
        now = time.monotonic() if now is None else now
        plugins = enabled(self.config, self.db, datetime.now(UTC))
        due: list[Enabled] = []
        for e in plugins:
            seconds = e.plugin.change_check_seconds
            last = self._last_checked.get(e.name)
            if seconds is None or e.name in self.refreshing:
                continue
            if last is None or now - last >= seconds:
                due.append(e)
        if not due:
            return set()
        graph, _ = stored_graph(self.db, plugins)  # once for every check due
        changed: dict[str, str] = {}
        for e in due:
            self._last_checked[e.name] = now
            if (why := self._changed(e, graph)) is not None:
                changed[e.name] = why
        if changed:
            log.info("changed: %s; refreshing", _said(changed))
            self.request_refresh(set(changed))
        return set(changed)

    def run_change_loop(self) -> None:
        """Check for changes (`check_for_changes`) every `CHANGE_LOOP_SECONDS`."""
        while True:
            try:
                self.check_for_changes()
            except Exception:
                log.exception("checking for changes failed")
            time.sleep(CHANGE_LOOP_SECONDS)

    def request_refresh(self, only: set[str] | None = None) -> None:
        """Ask the background loop to refresh `only` these plugins, or all of them, as soon as
        it can."""
        with self._pending_lock:
            if only is None or self._pending is None:
                self._pending = None
            else:
                self._pending |= only
        self._wake.set()

    def request_refresh_subjects(self, refs: list[Ref]) -> None:
        """Ask the background loop to fetch these entities again, ahead of any full refresh
        due (see `refresh.refresh_subjects`)."""
        with self._pending_lock:
            self._subjects = list(dict.fromkeys([*self._subjects, *refs]))
        self._wake.set()

    def refresh_subjects_now(self, refs: list[Ref]) -> set[str]:
        """Fetch these entities again, waiting for a refresh that's running (a subject's
        `store` mustn't race a full collect's); returns the plugins to refresh in full."""
        with self._refresh_lock:
            return refresh_subjects(self.config, self.db, refs)

    def _seconds_to_next(self) -> float:
        """How long until the next plugin is due, at least a second."""
        now = time.monotonic()
        waits = [
            self.last_refreshed.get(name, now) + seconds - now
            for name, seconds in self.intervals().items()
        ]
        return max(1.0, min(waits, default=60.0))

    def run_refresh_loop(self) -> None:
        """Refresh everything at start, then each plugin as it falls due, and what's asked
        for (`request_refresh`) at once; entities asked for (`request_refresh_subjects`)
        first, so a tab coming back to focus doesn't wait on a full collect."""
        only: set[str] | None = None
        subjects: list[Ref] = []
        while True:
            try:
                if subjects:
                    fallback = self.refresh_subjects_now(subjects)
                    only = None if only is None else only | fallback
                if only is None or only:
                    self.refresh_now(only)
            except Exception:
                log.exception("refresh failed")
            self._wake.wait(self._seconds_to_next())
            self._wake.clear()
            with self._pending_lock:
                pending, self._pending = self._pending, set()
                subjects, self._subjects = self._subjects, []
            only = None if pending is None else pending | self.due()

    def dashboard_page(
        self, notice: str = "", spot: render.Spot | None = None, tags: Sequence[str] = ()
    ) -> str:
        """The page from stored data, filtered by `tags` (see `tag_view`)."""
        built = build_dashboard(self.config, self.db, selected=tags)
        return render.dashboard_page(built, self.refreshing, notice, spot, tags)

    def run_action(
        self, name: str, fields: dict[str, str], back: str | None = None
    ) -> tuple[str, dict[str, str]]:
        """Run a button's action and refresh afterwards, so the page reflects it soon: the
        acting plugin, and what it says it touched (`ActionResult.touched`), fetched again
        through its publisher's `refresh_subjects`.
        `back` is the page the button was on, when it wasn't the dashboard.

        Returns the notice for the page and, when it succeeded, its redirect parameters.
        """
        try:
            result = perform_action(self.config, self.db, name, fields, back)
        except ActionError as e:
            return f"failed: {e}", {}
        plugin = name.partition("/")[0]
        if plugin != act_now.ACTIONS:  # Act now's own only change what's shown
            self.request_refresh({plugin})
            if result.touched:
                self.request_refresh_subjects(result.touched)
        return result.notice, result.params

    def take_notifications(self) -> str:
        """The events waiting to be told, as the JSON list of notifications the page shows
        (see `notifications`); they're marked told."""
        keep = timedelta(hours=self.config.notifications.keep_hours)
        found = NotificationRepository(self.db).take(datetime.now(UTC), keep)
        return json.dumps([n.model_dump() for n in found])

    def plugin_page(self, path: str, query: str, notice: str = "") -> str | Response | None:
        """An enabled plugin's page, with `notice` as its query's; None when there's none at
        `path`."""
        params = parse_qs(query, keep_blank_values=True)
        params = {k: v for k, v in params.items() if k != NOTICE} | (
            {NOTICE: [notice]} if notice else {}
        )
        return plugin_page(self.config, self.db, path, params)

    def focus_page(
        self,
        category: str,
        index: int,
        notice: str = "",
        kept: str = "",
        tags: Sequence[str] = (),
    ) -> str | None:
        """The category list without a category, else that category's item at `index`; with
        `tags`, of only what carries one of them (see `tag_view`).

        `kept` names an item an action asked to keep on the page, e.g. a todo just ticked off,
        so it can be undone; see `Plugin.kept_item`.
        """
        built = build_dashboard(self.config, self.db, surface="focus", selected=tags)
        if not category:
            return render.focus_categories_page(built, tags)
        done_item = kept_item(self.config, self.db, kept) if kept else None
        return render.focus_page(built, category, index, notice, done_item, tags)


def _said(changed: dict[str, str]) -> str:
    """The plugins that changed, each with why when it said, for the log."""
    return ", ".join(f"{name} ({why})" if why else name for name, why in sorted(changed.items()))


NOTICE = "notice"
PAGE_FAILED = "<p>This page failed; the log says why.</p>"
"""What a plugin's page answers when its handler raised."""
"""The query parameter a plugin's page reads its notice from (see `App.plugin_page`)."""


def _back_to_page(query: str, extra: dict[str, str] | None = None) -> str:
    """Where a POST redirects: the request's `back` path (a focus page) if it's local, else the
    dashboard, at the panel the request's `at` names, filtered by the request's tags (see
    `tag_view`; a `back` path has its own). The `extra` parameters ride along either way."""
    params: dict[str, str] = dict(extra or {})
    query_params = parse_qs(query, keep_blank_values=True)
    back = query_params.get("back", [""])[0]
    at = query_params.get("at", [""])[0]
    anchor = ""
    if AT_RE.fullmatch(at):  # the panel the button was in, and where its item was
        anchor = f"#{at}"
        if (spot := _spot(query_params)) and not render.local_path(back):
            params |= {"at": spot[0], "pos": str(spot[1])}
            anchor = f"#{render.SPOT_ID}"
    if render.local_path(back):
        return (
            back + ("&" if "?" in back else "?") + urlencode(params) if params else back
        ) + anchor
    return tag_view.url("/", tag_view.selected(query_params), params) + anchor


AT_RE = re.compile(r"[\w.-]+")
"""A panel's key, which a redirect may name in its `#` link."""

TOKEN_PARAM_RE = re.compile(r"([?&](?:token|code)=)[^&\s]*")
"""Query parameters that carry secrets: the dashboard's token, and `/login`'s one-time
code."""


def _redact(text: str) -> str:
    """`text` without the values of secret query parameters, for the log."""
    return TOKEN_PARAM_RE.sub(r"\1…", text)


def _spot(query_params: dict[str, list[str]]) -> render.Spot | None:
    """Where the request says an action was pressed (`at`, `pos`), if it says so validly."""
    at = query_params.get("at", [""])[0]
    pos = query_params.get("pos", [""])[0]
    return (at, int(pos)) if AT_RE.fullmatch(at) and pos.isdecimal() and len(pos) < 6 else None


def _int(query_params: dict[str, list[str]], name: str) -> int:
    try:
        return int(query_params.get(name, ["0"])[0])
    except ValueError:
        return 0


def _bad_response(response: Response) -> str | None:
    """What's wrong with a plugin page's `Response`, or None: a status HTTP has, and header
    names and values on one line each (a newline in one would let a value from the page's
    query, say a redirect's URL, add headers of its own)."""
    try:
        HTTPStatus(response.status)
    except ValueError:
        return f"no HTTP status {response.status}"
    texts = [response.content_type, *response.headers, *response.headers.values()]
    if any("\r" in text or "\n" in text for text in texts):
        return "a header with a line break"
    return None


def _form_fields(body: bytes) -> dict[str, str]:
    """The first value of each form field."""
    fields = parse_qs(body.decode(errors="replace"), keep_blank_values=True)
    return {k: v[0] for k, v in fields.items()}


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_SECONDS
        _notice_taken = False

        def handle(self) -> None:
            """A browser can cancel any request, including while headers or a body are
            being written. End that connection quietly; other server errors still surface."""
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
                log.debug("browser disconnected during a request")

        def end_headers(self) -> None:
            """Every answer, redirects and errors included, forbids being framed by another
            page, which would let it overlay the dashboard's buttons (clickjacking)."""
            for name, value in FRAME_HEADERS.items():
                self.send_header(name, value)
            super().end_headers()

        def log_message(self, format: str, *args: object) -> None:
            log.debug(format, *(_redact(str(a)) for a in args))

        def _send(
            self,
            status: HTTPStatus,
            body: str,
            content_type: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            data = body.encode()
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if self._notice_taken:  # one handler answers every request on its connection
                self.send_header("Set-Cookie", auth.CLEAR_NOTICE)
                self._notice_taken = False
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: HTTPStatus, body: str) -> None:
            self._send(status, body, "text/html; charset=utf-8")

        def _notification_stream(self) -> None:
            """Send notifications as server events while the tab stays connected. The
            heartbeat detects tabs that closed and keeps the connection from going idle."""
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while not shell.stopping():
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    found = app.take_notifications()
                    if found != "[]":
                        self.wfile.write(f"data: {found}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(NOTIFICATION_STREAM_SECONDS)
            except OSError:  # the tab closed or the browser suspended its connection
                pass

        def _redirect(self, location: str, notice: str = "") -> None:
            """Send the browser to `location`, which shows `notice` (see `_take_notice`)."""
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            if notice:
                cookie = auth.notice_cookie(app.token, notice)
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _take_notice(self) -> str:
            """The notice the last redirect left for this page, which it shows once: the
            answer drops it. A cookie, not a query parameter, so nobody else's link can put
            words in the dashboard's mouth, and a reload doesn't show it again."""
            notice = auth.notice_from(app.token, self.headers.get("Cookie"))
            self._notice_taken = notice is not None
            return notice or ""

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
            if auth.matches(app.token, auth.bearer(self.headers.get("Authorization"))):
                return False
            port = cast(HTTPServer, self.server).server_port
            session = auth.session_from(self.headers.get("Cookie"), port)
            if session and SessionRepository(app.db).valid(session, datetime.now(UTC)):
                return False
            self._html(HTTPStatus.UNAUTHORIZED, NOT_SIGNED_IN)
            return True

        def _login(self, query: str) -> None:
            """Sign the browser in with the one-time code `mfd open` got (see
            `auth.Handshake`): start a session, then go to the dashboard."""
            code = parse_qs(query).get("code", [""])[0]
            if not app.handshake.take_code(code):
                self._html(HTTPStatus.UNAUTHORIZED, NOT_SIGNED_IN)
                return
            port = cast(HTTPServer, self.server).server_port
            sessions = SessionRepository(app.db)
            sessions.prune(datetime.now(UTC))
            session = sessions.create(datetime.now(UTC), timedelta(days=auth.SESSION_DAYS))
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", auth.set_cookie(session, port))
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if self._refuse_other_sites(check_origin=False):
                return
            port = cast(HTTPServer, self.server).server_port
            if self.headers.get("Host") != f"{auth.HOST}:{port}" and not self.headers.get(
                "Authorization", ""
            ).startswith(auth.BEARER):
                # The session cookie is `auth.HOST`'s, and cookies are per host: send the
                # browser there, to the same path. Scripts with a token are served where
                # they are. The host is one of ours (checked above), so this can't send the
                # browser anywhere else.
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", f"{auth.origin(port)}{self.path}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            url = urlsplit(self.path)
            if url.path in STATIC_FILES:
                name, content_type = STATIC_FILES[url.path]
                self._send(
                    HTTPStatus.OK, (STATIC_DIR / name).read_text(encoding="utf-8"), content_type
                )
                return
            if url.path == LOGIN_PATH:
                self._login(url.query)
                return
            if self._refuse_strangers():
                return
            if url.path == NOTIFICATIONS_STREAM_PATH:
                self._notification_stream()
            elif url.path == "/":
                query = parse_qs(url.query)
                notice = self._take_notice()
                tags = tag_view.selected(query)
                self._html(HTTPStatus.OK, app.dashboard_page(notice, _spot(query), tags))
            elif url.path == settings.PATH and app.config_path is not None:
                self._html(HTTPStatus.OK, settings.page(app.config, self._take_notice()))
            elif url.path == "/focus":
                params = parse_qs(url.query, keep_blank_values=True)
                category = params.get("cat", [""])[0]
                notice = self._take_notice()
                kept = params.get(KEPT, [""])[0]
                tags = tag_view.selected(params)
                page = app.focus_page(category, _int(params, "i"), notice, kept, tags)
                if page is None:
                    self._html(HTTPStatus.NOT_FOUND, "<p>No such focus category.</p>")
                else:
                    self._html(HTTPStatus.OK, page)
            else:
                self._plugin_page(url.path, url.query, self._take_notice())

        def _plugin_page(self, path: str, query: str, notice: str) -> None:
            """Answer with a plugin's page: its HTML, or its `Response`."""
            try:
                page = app.plugin_page(path, query, notice)
            except PageFailed:
                self._html(HTTPStatus.INTERNAL_SERVER_ERROR, PAGE_FAILED)
                return
            if page is None:
                self._html(HTTPStatus.NOT_FOUND, "<p>Not found.</p>")
            elif isinstance(page, str):
                self._html(HTTPStatus.OK, page)
            elif (problem := _bad_response(page)) is not None:
                log.error("page %s answered a bad Response: %s", path, problem)
                self._html(HTTPStatus.INTERNAL_SERVER_ERROR, PAGE_FAILED)
            else:
                self._send(HTTPStatus(page.status), page.body, page.content_type, page.headers)

        def _read_body(self) -> bytes | None:
            """The request's body, read whole before anything is answered, refusals
            included: Windows resets a connection closed with a body unread, and the client
            can lose the answer. None when its Content-Length isn't a number from 0 to
            `MAX_BODY_BYTES`, which is answered with the connection closed."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                self.close_connection = True
                return None
            return self.rfile.read(length)

        def _json(self, status: HTTPStatus, data: dict[str, str]) -> None:
            self._send(status, json.dumps(data), "application/json")

        def _handshake(self, path: str, body: bytes) -> None:
            """`mfd open`'s two steps (see `auth.Handshake`): a nonce for the client to answer,
            then, for a client whose answer shows it knows the token, a login code and the
            server's own proof of knowing it. They carry no token and need no session."""
            if path == LOGIN_CHALLENGE_PATH:
                nonce = app.handshake.nonce()
                if nonce is None:
                    self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "try again shortly"})
                else:
                    self._json(HTTPStatus.OK, {"nonce": nonce})
                return
            try:
                fields = json.loads(body)
                nonce, theirs, given = fields["nonce"], fields["client_nonce"], fields["proof"]
                if not all(isinstance(v, str) for v in (nonce, theirs, given)):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request"})
                return
            expected = auth.proof(app.token, "client", nonce, theirs)
            # The nonce is used up whatever the answer, so each can be tried once.
            fresh = app.handshake.take_nonce(nonce)
            code = app.handshake.code() if fresh and auth.matches(expected, given) else None
            if code is None:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "not signed in"})
                return
            server_proof = auth.proof(app.token, "server", theirs, code)
            self._json(HTTPStatus.OK, {"code": code, "proof": server_proof})

        def do_POST(self) -> None:
            body = self._read_body()
            if self._refuse_other_sites(check_origin=True):
                return
            if body is not None and urlsplit(self.path).path in HANDSHAKE_PATHS:
                self._handshake(urlsplit(self.path).path, body)
                return
            if self._refuse_strangers():
                return
            if body is None:
                self._html(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "<p>Too large.</p>")
                return
            url = urlsplit(self.path)
            if url.path == "/refresh":
                plugin = _form_fields(body).get("plugin")
                if plugin is None:
                    app.request_refresh()
                elif plugin in app.refreshable():
                    app.request_refresh({plugin})
                else:
                    self._html(HTTPStatus.BAD_REQUEST, "<p>No such plugin to refresh.</p>")
                    return
                self._redirect(_back_to_page(url.query))
            elif url.path == SUBJECTS_PATH:
                try:
                    refs = _SUBJECTS.validate_json(body)
                except ValidationError:
                    self._html(HTTPStatus.BAD_REQUEST, "<p>Not a list of entities.</p>")
                    return
                app.request_refresh_subjects(refs)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            elif url.path == NOTIFICATIONS_PATH:
                self._send(HTTPStatus.OK, app.take_notifications(), "application/json")
            elif url.path == "/layout":
                try:
                    layout = Layout.model_validate_json(body)
                except ValidationError:
                    self._html(HTTPStatus.BAD_REQUEST, "<p>Not a layout.</p>")
                    return
                LayoutRepository(app.db).save(layout)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            elif url.path == settings.PATH and app.config_path is not None:
                notice = settings.save(app.config_path, app.config, _form_fields(body))
                self._redirect(settings.PATH, notice)
            elif url.path == "/layout/reset":
                LayoutRepository(app.db).reset()
                self._redirect(_back_to_page(url.query), "layout reset")
            elif url.path.startswith("/actions/"):
                name = url.path[len("/actions/") :]
                fields = _form_fields(body)
                back = parse_qs(url.query).get("back", [""])[0]
                notice, extra = app.run_action(
                    name, fields, back if render.local_path(back) else None
                )
                self._redirect(_back_to_page(url.query, extra), notice)
            else:
                self._html(HTTPStatus.NOT_FOUND, "<p>Not found.</p>")

    return Handler


class _HTTPServer(ThreadingHTTPServer):
    """Serves HTTP, and is refused a port something else is listening on. `HTTPServer` sets
    `SO_REUSEADDR`, which elsewhere only lets a restart rebind past `TIME_WAIT`. On Windows,
    which has no need of it for that, it lets sockets share a port: two dashboards setting it
    would both bind, and binding over any other listener fails as `WSAEACCES`, not as in use."""

    allow_reuse_address = sys.platform != "win32"


def serve(
    config: Config,
    db: Database,
    token: str,
    port: int | None = None,
    config_path: Path | None = None,
    on_listening: Callable[[], object] | None = None,
) -> None:
    """Serve forever on localhost over HTTP, refreshing in the background. The settings page
    saves into `config_path`. `on_listening` runs in a thread once the port is bound (`mfd
    serve --open` signs a browser in)."""
    if port and port != config.port:  # `--port`: plugins build URLs back to the dashboard
        config = config.model_copy(update={"port": port})
    app = App(config, db, token, config_path, auth.TokenFile(auth.token_path(config.db_path)))
    server = _HTTPServer(("127.0.0.1", port or config.port), make_handler(app))
    threading.Thread(target=app.run_refresh_loop, name="refresh", daemon=True).start()
    threading.Thread(target=app.run_change_loop, name="changes", daemon=True).start()
    log.info("serving on http://127.0.0.1:%d", server.server_port)
    if on_listening is not None:
        threading.Thread(target=on_listening, name="listening", daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Stopped by the `mfd serve` parent (see `supervisor`). The interpreter waits for a refresh
        # in flight; `shell.stop` makes it wind down fast.
        log.info("shutting down…")
        shell.stop()
    finally:
        server.server_close()
