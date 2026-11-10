import errno
import json
import logging
import socket
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import pytest
from conftest import ME, NOW, pr, snapshot

from move_forward import auth, registry, server, settings
from move_forward.change_checks import ChangeChecks
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph, Ref
from move_forward.notifications import Event, NotificationRepository
from move_forward.page import Action, Item
from move_forward.panels import Layout
from move_forward.plugin import (
    KEPT,
    ActionError,
    ActionResult,
    Changed,
    ChangedRequest,
    PageRequest,
    Response,
)
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.plugin import SlackPlugin
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.registry import migrate
from move_forward.repositories import LayoutRepository, SessionRepository, TagRepository


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> server.App:
    config = Config.model_validate(
        {
            "email": "me@acme.com",
            "github": GitHubConfig(org="acme"),
            "teams": ["core"],
            "slack": SlackConfig(),
            "db_path": tmp_path / "x.db",
        }
    )
    db = Database(":memory:")
    migrate(config, db)
    PrRepository(db).replace(
        snapshot(mine=[pr(1, requested_users=["alice"], requested_teams=["web"])])
    )
    refreshed: list[bool] = []

    def fake_refresh(*_: object) -> None:
        refreshed.append(True)

    monkeypatch.setattr(server, "refresh", fake_refresh)
    instance = server.App(config, db, TOKEN)
    instance.refreshed = refreshed  # type: ignore[attr-defined]
    return instance


@pytest.fixture
def client(app: server.App) -> Iterator[HTTPConnection]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
    yield connection
    connection.close()
    httpd.shutdown()


TOKEN = "s3cret"
SIGNED_IN = {"Authorization": f"Bearer {TOKEN}"}


def browser(client: HTTPConnection, **headers: str) -> dict[str, str]:
    """A browser's headers: it reaches the dashboard as `auth.HOST`, with `headers`."""
    return {"Host": f"{auth.HOST}:{client.port}", **headers}


def notice_cookie(response: HTTPResponse) -> str:
    """The notice cookie a redirect set, as a `Cookie` header's value."""
    return (response.getheader("Set-Cookie") or "").partition(";")[0]


def notice_of(response: HTTPResponse) -> str | None:
    """The notice a redirect left for the next page."""
    response.read()
    return auth.notice_from(TOKEN, notice_cookie(response))


def get(client: HTTPConnection, path: str, headers: dict[str, str] | None = None) -> HTTPResponse:
    """A GET with the token, unless `headers` says otherwise."""
    client.request("GET", path, headers=SIGNED_IN if headers is None else headers)
    return client.getresponse()


def post(
    client: HTTPConnection, path: str, form: dict[str, str], headers: dict[str, str] | None = None
) -> HTTPResponse:
    """A form POST with the token, plus `headers`."""
    body = urlencode(form)
    client.request(
        "POST",
        path,
        body=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **SIGNED_IN,
            **(headers or {}),
        },
    )
    return client.getresponse()


FakeRun = Callable[[object, object, str, dict[str, str]], str]


def _results(
    run: FakeRun,
) -> Callable[[object, object, str, dict[str, str], str | None], ActionResult]:
    """An action runner standing in for `perform_action`, from one that returns notices."""

    def perform(
        config: object, db: object, name: str, fields: dict[str, str], back: str | None = None
    ) -> ActionResult:
        return ActionResult(run(config, db, name, fields))

    return perform


def test_dashboard_page_renders_panels_and_ping_links(client: HTTPConnection) -> None:
    response = get(client, "/")
    body = response.read().decode()
    assert response.status == 200
    assert "Needs a reviewer" in body
    assert f"as GitHub: {ME}" in body
    assert "/slack/ping?pr=" + quote(pr(1).url, safe="") in body


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_a_browser_disconnect_ends_the_connection_quietly(
    app: server.App, monkeypatch: pytest.MonkeyPatch, error: type[ConnectionError]
) -> None:
    def disconnected(_self: BaseHTTPRequestHandler) -> None:
        raise error("browser closed the connection")

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle_one_request", disconnected)
    handler_type = server.make_handler(app)
    handler = handler_type.__new__(handler_type)
    handler.handle()
    assert handler.close_connection


def test_other_request_errors_still_surface(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(_self: BaseHTTPRequestHandler) -> None:
        raise OSError("unexpected server failure")

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle_one_request", failed)
    handler_type = server.make_handler(app)
    handler = handler_type.__new__(handler_type)
    with pytest.raises(OSError, match="unexpected server failure"):
        handler.handle()


def test_stylesheet_and_404(client: HTTPConnection) -> None:
    assert get(client, "/style.css").status == 200
    favicon = get(client, "/favicon.svg")
    assert favicon.status == 200 and favicon.getheader("Content-Type") == "image/svg+xml"
    assert favicon.read().startswith(b"<svg")
    refresh_icon = get(client, "/favicon-refresh.svg")
    assert refresh_icon.status == 200
    assert refresh_icon.getheader("Content-Type") == "image/svg+xml"
    first = refresh_icon.read()
    assert b"M1.3 5.1h1.1v1.3H1.3z' fill='#a78bfa'" in first
    assert first.count(b"<svg") == 1
    ElementTree.fromstring(first)
    for path, dot in (
        ("/favicon-refresh-1.svg", b"M3.3 5.1h1.1v1.3H3.3z' fill='#a78bfa'"),
        ("/favicon-refresh-2.svg", b"M5.3 5.1h1.1v1.3H5.3z' fill='#a78bfa'"),
    ):
        frame = get(client, path)
        assert frame.status == 200 and frame.getheader("Content-Type") == "image/svg+xml"
        body = frame.read()
        assert dot in body
        assert body.count(b"<svg") == 1
        ElementTree.fromstring(body)
    assert b"<circle" not in get(client, "/logo.svg").read()
    assert get(client, "/nope").status == 404


def test_ping_page_lists_targets(client: HTTPConnection) -> None:
    response = get(client, "/slack/ping?pr=" + quote(pr(1).url, safe=""))
    body = response.read().decode()
    assert response.status == 200
    assert "alice (DM)" in body and "team web" in body
    assert "copyMessage" in body
    assert get(client, "/ping?pr=https://github.com/acme/widgets/pull/99").status == 404


def test_mark_as_sent_records_a_ping_and_redirects(client: HTTPConnection, app: server.App) -> None:
    response = post(
        client,
        "/actions/slack/mark_sent",
        {"pr_url": pr(1).url, "target": "user:alice", "kind": "first", "message": "hi <there>"},
    )
    assert response.status == 303
    assert response.getheader("Location") == "/"
    assert notice_of(response) == "marked the first ping to alice as sent"
    pings = SlackRepository(app.db).pings()
    assert [(p.target, p.kind, p.message) for p in pings] == [("user:alice", "first", "hi <there>")]
    # The next page shows the follow-up.
    body = get(client, "/slack/ping?pr=" + quote(pr(1).url, safe="")).read().decode()
    assert "Gentle bump" in body


SENT = {"pr_url": pr(1).url, "target": "user:alice", "kind": "first"}


def test_a_post_from_another_site_is_refused(client: HTTPConnection, app: server.App) -> None:
    response = post(client, "/actions/slack/mark_sent", SENT, {"Origin": "https://evil.example"})
    assert response.status == 403
    response.read()
    response = post(client, "/actions/slack/mark_sent", SENT, {"Origin": "null"})
    assert response.status == 403
    response.read()
    assert SlackRepository(app.db).pings() == []


def test_a_post_from_the_dashboard_itself_is_served(
    client: HTTPConnection, app: server.App
) -> None:
    origin = f"http://localhost:{client.port}"
    response = post(client, "/actions/slack/mark_sent", SENT, {"Origin": origin})
    assert response.status == 303
    assert len(SlackRepository(app.db).pings()) == 1


def test_a_request_naming_another_host_is_refused(client: HTTPConnection) -> None:
    client.putrequest("GET", "/", skip_host=True)
    client.putheader("Host", f"rebound.example:{client.port}")
    client.endheaders()
    response = client.getresponse()
    assert response.status == 403
    response.read()


def test_invalid_ping_form_is_rejected(client: HTTPConnection, app: server.App) -> None:
    response = post(
        client, "/actions/slack/mark_sent", {"pr_url": "x", "target": "t", "kind": "later"}
    )
    assert response.status == 303 and "failed: bad ping" in (notice_of(response) or "")
    for target in ("alice", "robot:alice", "user:"):
        response = post(client, "/actions/slack/mark_sent", {**SENT, "target": target})
        assert "failed: bad ping" in (notice_of(response) or ""), target
    assert SlackRepository(app.db).pings() == []


def test_actions_run_and_redirect_with_a_notice(
    client: HTTPConnection, app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def fake_run(_config: object, _db: object, name: str, fields: dict[str, str]) -> str:
        seen.append((name, fields))
        if name == "boom":
            raise ActionError("nope")
        return "did it"

    monkeypatch.setattr(server, "perform_action", _results(fake_run))
    response = post(client, "/actions/delete_branch", {"repo": "/r", "branch": "b"})
    assert response.status == 303
    assert (response.getheader("Location"), notice_of(response)) == ("/", "did it")
    assert seen == [("delete_branch", {"repo": "/r", "branch": "b"})]
    assert app._wake.is_set()  # pyright: ignore[reportPrivateUsage]

    response = post(client, "/actions/boom", {})
    assert (response.getheader("Location"), notice_of(response)) == ("/", "failed: nope")
    cookie = {**SIGNED_IN, "Cookie": notice_cookie(response)}
    shown = get(client, "/", cookie)
    assert "<span class='notice'>failed: nope</span>" in shown.read().decode()
    assert shown.getheader("Set-Cookie") == auth.CLEAR_NOTICE  # shown once


def test_refresh_wakes_the_loop(client: HTTPConnection, app: server.App) -> None:
    assert post(client, "/refresh", {}).status == 303
    assert app.refresh_now() is True
    assert app.refreshed == [True]  # type: ignore[attr-defined]


def test_refresh_can_be_asked_for_one_plugin(client: HTTPConnection, app: server.App) -> None:
    assert app.refreshable() == {"github"}  # slack collects nothing
    assert post(client, "/refresh", {"plugin": "github"}).status == 303
    assert app._pending == {"github"}  # pyright: ignore[reportPrivateUsage]
    response = post(client, "/refresh", {"plugin": "jira"})  # not enabled
    assert response.status == 400
    response.read()
    assert app._pending == {"github"}  # pyright: ignore[reportPrivateUsage]
    assert post(client, "/refresh", {}).status == 303
    assert app._pending is None  # pyright: ignore[reportPrivateUsage]


def test_focus_pages(client: HTTPConnection) -> None:
    body = get(client, "/focus").read().decode()
    assert "What to focus on?" in body and "href='/focus?cat=act&amp;i=0'" in body
    body = get(client, "/focus?cat=act&i=0").read().decode()
    assert "class='focus-position'>1 of " in body and pr(1).title in body
    assert get(client, "/focus?cat=act&i=x").status == 200
    assert get(client, "/focus?cat=nope").status == 404
    assert get(client, "/focus?cat=all").status == 404


def test_actions_from_a_focus_page_redirect_back_to_it(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_: object) -> str:
        return "did it"

    monkeypatch.setattr(server, "perform_action", _results(fake_run))
    response = post(client, "/actions/x?back=%2Ffocus%3Fcat%3Dact%26i%3D0", {})
    assert (response.getheader("Location"), notice_of(response)) == ("/focus?cat=act&i=0", "did it")
    # Only local paths; anything else goes to the dashboard.
    response = post(client, "/actions/x?back=//evil.example", {})
    assert (response.getheader("Location"), notice_of(response)) == ("/", "did it")
    response = post(client, "/actions/x?back=https%3A%2F%2Fevil.example", {})
    assert (response.getheader("Location"), notice_of(response)) == ("/", "did it")
    response = post(client, "/actions/x?back=%2F%5Cevil.example", {})  # browsers read "/\\" as "//"
    assert (response.getheader("Location"), notice_of(response)) == ("/", "did it")


def test_actions_redirect_to_the_panel_they_were_in(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_: object) -> str:
        return "did it"

    monkeypatch.setattr(server, "perform_action", _results(fake_run))
    response = post(client, "/actions/x?at=jira.jira_next", {})  # a panel's own button
    assert (response.getheader("Location"), notice_of(response)) == ("/#jira.jira_next", "did it")
    response = post(client, "/actions/x?at=jira.jira_next&pos=3", {})  # an item's
    assert (response.getheader("Location"), notice_of(response)) == (
        "/?at=jira.jira_next&pos=3#spot",
        "did it",
    )
    response = post(client, "/actions/x?at=a%22%3E%3Cscript", {})  # only a panel's key
    assert (response.getheader("Location"), notice_of(response)) == ("/", "did it")


def test_the_page_and_focus_mode_are_filtered_by_the_tags_in_their_address(
    client: HTTPConnection, app: server.App
) -> None:
    TagRepository(app.db).add(Ref(kind="pull_request", id=pr(1).url), "chores", NOW)
    # What the page's reload fetches again: its own address.
    assert pr(1).title in get(client, "/?tag=chores").read().decode()
    body = get(client, "/?tag=bins&tag=%27%3E%3Cb%3E").read().decode()
    assert pr(1).title not in body and "Nothing carries #bins or #b." in body
    assert "'><b>" not in body
    body = get(client, "/focus?cat=act&i=0&tag=chores").read().decode()
    assert "1 of 1" in body and "href='/focus?tag=chores'>focus</a>" in body
    assert "Nothing carries #bins" in get(client, "/focus?tag=bins").read().decode()


def test_a_buttons_redirect_keeps_the_tags_the_page_is_filtered_by(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_: object) -> str:
        return "did it"

    monkeypatch.setattr(server, "perform_action", _results(fake_run))
    response = post(client, "/actions/x?at=github.ready&pos=3&tag=chores&tag=Garbage+Day", {})
    assert (response.getheader("Location"), notice_of(response)) == (
        "/?at=github.ready&pos=3&tag=chores&tag=garbage-day#spot",
        "did it",
    )
    response = post(client, "/refresh?tag=chores", {})
    response.read()
    assert response.getheader("Location") == "/?tag=chores"
    # A focus page's address has its own.
    response = post(client, "/actions/x?back=%2Ffocus%3Fcat%3Dact%26tag%3Dchores&tag=x", {})
    assert response.getheader("Location") == "/focus?cat=act&tag=chores"
    response.read()


def test_ticking_a_todo_off_redirects_back_with_it(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_perform(
        _config: object, _db: object, name: str, fields: dict[str, str], back: str | None = None
    ) -> ActionResult:
        params = {KEPT: "notes:7"} if name == "notes/todo_done" else {}
        return ActionResult(f"{name} {fields['todo']}", params)

    monkeypatch.setattr(server, "perform_action", fake_perform)
    kept = Item(
        title="x y",
        actions=[
            Action(
                name="notes/todo_undone",
                label="Done",
                fields={"todo": "7"},
                checkbox=True,
                checked=True,
            )
        ],
    )

    def fake_kept(*_: object) -> tuple[str, Item]:
        return "notes.todos", kept

    monkeypatch.setattr(server, "kept_item", fake_kept)
    response = post(
        client,
        "/actions/notes/todo_done?back=%2Ffocus%3Fcat%3Dnotes.todos%26i%3D0",
        {"todo": "7"},
    )
    assert response.getheader("Location") == "/focus?cat=notes.todos&i=0&kept=notes%3A7"
    assert notice_of(response) == "notes/todo_done 7"
    body = get(client, "/focus?cat=notes.todos&i=0&kept=notes%3A7").read().decode()
    assert "<input type='checkbox' checked" in body and "x y" in body
    response = post(
        client,
        "/actions/notes/todo_undone?back=%2Ffocus%3Fcat%3Dnotes.todos%26i%3D0",
        {"todo": "7"},
    )
    assert response.getheader("Location") == "/focus?cat=notes.todos&i=0"
    assert notice_of(response) == "notes/todo_undone 7"


def test_being_stopped_shuts_down_quietly(
    app: server.App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    closed = False

    class FakeServer:
        server_port = 1

        def __init__(self, *_: object) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(server, "_HTTPServer", FakeServer)
    monkeypatch.setattr(server.shell, "_stopping", threading.Event())  # don't stop other tests

    def no_start(_self: object) -> None:
        pass  # no refresh thread; the fake server never waits for it anyway

    monkeypatch.setattr(server.threading.Thread, "start", no_start)
    with caplog.at_level("INFO"):
        server.serve(app.config, app.db, TOKEN, 1)
    assert closed and "shutting down" in caplog.text
    assert server.shell.stopping()
    with pytest.raises(server.shell.CommandError, match="shutting down"):
        server.shell.run(["true"])


def test_busy_port_does_not_start_refresh_threads(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()

        def unexpected_start(_self: threading.Thread) -> None:
            pytest.fail("a refresh thread started before the server bound its port")

        monkeypatch.setattr(server.threading.Thread, "start", unexpected_start)
        with pytest.raises(OSError) as error:
            server.serve(app.config, app.db, TOKEN, occupied.getsockname()[1])
        assert error.value.errno == errno.EADDRINUSE


def test_two_dashboards_cannot_share_a_port(app: server.App) -> None:
    first = server._HTTPServer(("127.0.0.1", 0), server.make_handler(app))  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(OSError) as error:
            server._HTTPServer(("127.0.0.1", first.server_port), server.make_handler(app))  # pyright: ignore[reportPrivateUsage]
        assert error.value.errno == errno.EADDRINUSE
    finally:
        first.server_close()


def test_a_request_without_the_token_is_refused(client: HTTPConnection, app: server.App) -> None:
    wrong_session = f"{auth.session_cookie(client.port)}=wrong"
    for headers in (
        browser(client),
        browser(client, Authorization="Bearer wrong"),
        browser(client, Cookie=wrong_session),
    ):
        for path in ("/", "/slack/contacts", "/slack/callback"):  # plugins' pages, sign-in's too
            response = get(client, path, headers)
            assert response.status == 401 and "mfd open" in response.read().decode()
    response = post(client, "/actions/slack/mark_sent", SENT, {"Authorization": "Bearer wrong"})
    assert response.status == 401
    response.read()
    assert SlackRepository(app.db).pings() == []


def test_static_files_need_no_token(client: HTTPConnection) -> None:
    response = get(client, "/style.css", browser(client))
    assert response.status == 200
    response.read()


def _login_code(client: HTTPConnection, token: str = TOKEN) -> tuple[str, str]:
    """`mfd open`'s handshake against the server: (code, the server's proof of the token)."""
    client.request("POST", "/login/challenge", headers=browser(client))
    nonce = json.loads(client.getresponse().read())["nonce"]
    mine = "my-nonce"
    body = json.dumps(
        {"nonce": nonce, "client_nonce": mine, "proof": auth.proof(token, "client", nonce, mine)}
    )
    client.request("POST", "/login/code", body=body, headers=browser(client))
    answer = json.loads(client.getresponse().read())
    assert auth.matches(auth.proof(token, "server", mine, answer["code"]), answer["proof"])
    return answer["code"], answer["proof"]


def test_login_signs_the_browser_in_with_a_one_time_code_and_a_session(
    client: HTTPConnection, app: server.App
) -> None:
    response = get(client, f"/login?token={TOKEN}", browser(client))  # the token is no code
    assert response.status == 401 and "Set-Cookie" not in response.headers
    response.read()
    code, _ = _login_code(client)
    response = get(client, f"/login?code={code}", browser(client))
    assert response.status == 303 and response.getheader("Location") == "/"
    cookie = response.getheader("Set-Cookie") or ""
    name = auth.session_cookie(client.port)
    assert cookie.startswith(f"{name}=") and TOKEN not in cookie  # an id, not the token
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Domain" not in cookie
    response.read()
    response = get(client, "/", browser(client, Cookie=cookie.split(";")[0]))
    assert response.status == 200
    response.read()
    response = get(client, f"/login?code={code}", browser(client))  # a code works once
    assert response.status == 401
    response.read()
    assert SessionRepository(app.db).revoke_all() == 1  # `mfd logout`
    response = get(client, "/", browser(client, Cookie=cookie.split(";")[0]))
    assert response.status == 401
    response.read()


def test_the_handshake_gives_a_code_only_to_a_client_that_knows_the_token(
    client: HTTPConnection,
) -> None:
    client.request("POST", "/login/challenge", headers=browser(client))
    nonce = json.loads(client.getresponse().read())["nonce"]

    def ask(nonce: str, proof: str) -> HTTPResponse:
        body = json.dumps({"nonce": nonce, "client_nonce": "n", "proof": proof})
        client.request("POST", "/login/code", body=body, headers=browser(client))
        return client.getresponse()

    wrong = ask(nonce, auth.proof("another token", "client", nonce, "n"))
    assert wrong.status == 401 and b"code" not in wrong.read()
    # The nonce is used up by the failed try, even for the right answer.
    right = ask(nonce, auth.proof(TOKEN, "client", nonce, "n"))
    assert right.status == 401
    right.read()
    client.request("POST", "/login/code", body="not json", headers=browser(client))
    assert client.getresponse().status == 400
    client.request("POST", "/login/challenge", headers={"Host": "evil.example"})
    assert client.getresponse().status == 403  # not from this dashboard


def test_browsers_are_sent_to_the_dashboards_own_host_but_scripts_are_served_where_they_are(
    client: HTTPConnection,
) -> None:
    canonical = f"http://{auth.HOST}:{client.port}"
    for host in (f"localhost:{client.port}", f"127.0.0.1:{client.port}"):
        response = get(client, "/focus?cat=act&i=2", {"Host": host})
        assert response.status == 302
        assert response.getheader("Location") == f"{canonical}/focus?cat=act&i=2"
        response.read()
        response = get(client, "//evil.example/x", {"Host": host})
        assert response.getheader("Location", "").startswith(f"{canonical}/")
        response.read()
    script = get(client, "/", {"Host": f"127.0.0.1:{client.port}", **SIGNED_IN})
    assert script.status == 200  # a token in a header, so no browser: served here
    script.read()


def test_the_login_code_stays_out_of_the_log(
    client: HTTPConnection, caplog: pytest.LogCaptureFixture
) -> None:
    code, _ = _login_code(client)
    with caplog.at_level("DEBUG", logger="move_forward.server"):
        get(client, f"/login?code={code}", browser(client)).read()
    assert "/login?code=" in caplog.text and code not in caplog.text


def _post_layout(
    client: HTTPConnection, body: str, headers: dict[str, str] | None = None
) -> HTTPResponse:
    client.request(
        "POST",
        "/layout",
        body=body,
        headers={"Content-Type": "application/json", **SIGNED_IN, **(headers or {})},
    )
    return client.getresponse()


def test_the_page_keeps_the_order_dragged_into_it_until_reset(
    client: HTTPConnection, app: server.App
) -> None:
    panels = '["github.held", "github.metadata", "github.needs_reviewer", "github.team_prs"]'
    layout = f'{{"groups": ["slack", "github"], "panels": {{"github": {panels}}}}}'
    response = _post_layout(client, layout)
    assert response.status == 204
    response.read()
    page = get(client, "/").read().decode()
    assert page.index("data-key='slack'") < page.index("data-key='github'")
    assert page.index("data-key='github.held'") < page.index("data-key='github.metadata'")
    response = post(client, "/layout/reset", {})
    assert response.status == 303 and (response.getheader("Location"), notice_of(response)) == (
        "/",
        "layout reset",
    )
    response.read()
    assert LayoutRepository(app.db).load() == Layout()


def test_a_layout_is_refused_from_another_site_without_the_token_or_malformed(
    client: HTTPConnection, app: server.App
) -> None:
    layout = '{"groups": ["slack"]}'
    for headers in ({"Origin": "https://evil.example"}, {"Authorization": "Bearer wrong"}):
        response = _post_layout(client, layout, headers)
        assert response.status in (401, 403)
        response.read()
    for body in ("not json", '{"groups": "slack"}', '{"groups": ["' + "x" * 300 + '"]}'):
        response = _post_layout(client, body)
        assert response.status == 400
        response.read()
    assert LayoutRepository(app.db).load() == Layout()


@pytest.mark.parametrize("length", [str(server.MAX_BODY_BYTES + 1), "-1", "lots"])
def test_a_post_whose_length_is_too_large_or_not_a_length_is_refused(
    client: HTTPConnection, length: str
) -> None:
    client.putrequest("POST", "/layout")
    for name, value in SIGNED_IN.items():
        client.putheader(name, value)
    client.putheader("Content-Length", length)
    client.endheaders()
    response = client.getresponse()
    assert response.status == 413 and response.getheader("Connection") == "close"
    response.read()


def test_the_loop_refreshes_each_plugin_as_it_falls_due(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    app.config = app.config.with_section("git", GitConfig(repos=[], refresh_seconds=60))
    migrate(app.config, app.db)  # git's tables, as `mfd serve` makes them
    assert app.intervals() == {"github": 300, "git": 60}
    assert app.due() == {"github", "git"}  # never refreshed
    app.refresh_now()
    assert app.due() == set()
    clock[0] += 61
    assert app.due() == {"git"} and app._seconds_to_next() == 1.0  # pyright: ignore[reportPrivateUsage]
    clock[0] += 240
    assert app.due() == {"git", "github"}
    app.refresh_now({"git"})
    assert app.due() == {"github"}


def test_a_refresh_is_given_the_servers_change_checks(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    given: list[ChangeChecks | None] = []

    def recording(
        config: Config,
        db: Database,
        now: datetime | None = None,
        only: set[str] | None = None,
        checks: ChangeChecks | None = None,
    ) -> None:
        given.append(checks)

    monkeypatch.setattr(server, "refresh", recording)
    app.refresh_now({"github"})
    # Its collects are asked what moved as they start, and kept with what its checks said.
    assert len(given) == 1 and given[0] is app.checks


def test_a_page_load_asks_for_no_refresh(app: server.App, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    app.refresh_now()
    clock[0] += 299  # past a minute, but a second short of github's 300 s: not due
    app.dashboard_page()
    app.focus_page("", 0)
    assert app._pending == set()  # pyright: ignore[reportPrivateUsage]


class _GitHubWithChecks(GitHubPlugin):
    """github whose change check answers from `answers`, raising for an exception."""

    change_check_seconds: ClassVar[int | None] = 60
    answers: ClassVar[list[bool | Changed | Exception]] = []
    """Each a `Changed`, or a bare bool for one without a reason."""

    def changed(self, req: ChangedRequest[GitHubConfig]) -> Changed:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, Changed) else Changed(everything=answer)


def test_a_plugin_that_changed_is_refreshed_without_waiting_for_its_interval(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubWithChecks()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setattr(_GitHubWithChecks, "answers", [False, RuntimeError("offline"), True])
    assert app.check_for_changes(1000.0) == set()
    assert app.check_for_changes(1030.0) == set()  # not asked: 60s haven't passed
    assert app.check_for_changes(1060.0) == set()  # the check failed; tried again later
    assert app.check_for_changes(1120.0) == {"github"}
    assert app._pending == {"github"}  # pyright: ignore[reportPrivateUsage]
    # A refresh starts the wait over, and asks nothing itself: its collect is asked as it
    # starts (see `refresh`), and what moves after that counts at the next check.
    monkeypatch.setattr(_GitHubWithChecks, "answers", [True])
    monkeypatch.setattr(server.time, "monotonic", lambda: 2000.0)
    app.refresh_now({"github"})
    assert _GitHubWithChecks.answers == [True]
    assert app.check_for_changes(2030.0) == set() and _GitHubWithChecks.answers == [True]
    assert app.check_for_changes(2060.0) == {"github"}


def test_why_a_plugin_changed_is_logged(
    app: server.App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    swap = {"github": _GitHubWithChecks()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setattr(
        _GitHubWithChecks,
        "answers",
        [Changed(everything=True, reason="2 PRs notified"), Changed(reason="x")],
    )
    with caplog.at_level(logging.INFO):
        assert app.check_for_changes(1000.0) == {"github"}
        assert app.check_for_changes(1060.0) == set()  # said no, whatever its reason
    assert "changed: github (2 PRs notified); refreshing" in caplog.messages


class _GitHubComparingGraphs(GitHubPlugin):
    """github whose change check notes the graphs it's given, and raises while `failing`."""

    change_check_seconds: ClassVar[int | None] = 60
    given: ClassVar[list[tuple[Graph | None, Graph]]] = []
    failing: ClassVar[list[bool]] = []

    def changed(self, req: ChangedRequest[GitHubConfig]) -> Changed:
        self.given.append((req.previous_graph, req.graph))
        if self.failing.pop(0):
            raise RuntimeError("offline")
        return Changed()


def test_a_change_check_is_given_the_graph_of_the_last_that_didnt_fail(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubComparingGraphs()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    monkeypatch.setattr(_GitHubComparingGraphs, "given", [])
    monkeypatch.setattr(_GitHubComparingGraphs, "failing", [False, True, False])
    for now in (1000.0, 1060.0, 1120.0):
        app.check_for_changes(now)
    [(first, seen), (second, failed), (third, _)] = _GitHubComparingGraphs.given
    assert first is None  # nothing to compare with since the start
    assert second is seen and failed is not seen  # a graph built for each check
    assert third is seen  # the failed check's graph isn't the one compared with


class _SlackChanging(SlackPlugin):
    change_check_seconds: ClassVar[int | None] = 60

    def changed(self, req: ChangedRequest[SlackConfig]) -> Changed:
        return Changed(everything=True, reason="a message")


class _GitHubAnsweringBadly(GitHubPlugin):
    change_check_seconds: ClassVar[int | None] = 60

    def changed(self, req: ChangedRequest[GitHubConfig]) -> Changed:
        return True  # pyright: ignore[reportReturnType]  # not a Changed


def test_a_malformed_change_check_is_a_failed_one(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped, as a check that raises is, while the others' changes still count."""
    # Slack is checked after github (by name), so its change counts only if the round goes on.
    swap = {"github": _GitHubAnsweringBadly(), "slack": _SlackChanging()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    assert app.check_for_changes(1000.0) == {"slack"}


class _GitHubWatchingGit(_GitHubWithChecks):
    change_check_seconds: ClassVar[int | None] = None
    watches: ClassVar[list[str]] = ["git"]


def test_a_plugin_watching_another_is_asked_right_after_that_one_refreshes(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubWatchingGit()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    app.config = app.config.with_section("git", GitConfig(repos=[]))
    migrate(app.config, app.db)
    monkeypatch.setattr(_GitHubWatchingGit, "answers", [True, False])
    app.refresh_now({"git"})
    assert app._pending == {"github"}  # pyright: ignore[reportPrivateUsage]
    app._pending = set()  # pyright: ignore[reportPrivateUsage]
    app.refresh_now({"github"})  # its own refresh: not asked here (its collect is)
    app.refresh_now({"git"})  # nothing of github's moved
    assert app._pending == set() and _GitHubWatchingGit.answers == []  # pyright: ignore[reportPrivateUsage]
    assert app.check_for_changes() == set()  # no change_check_seconds: never polled


def test_secrets_in_query_parameters_are_left_out_of_the_log() -> None:
    line = "GET /login?code=abc&ok=1 /?token=t0k HTTP/1.1"
    assert server._redact(line) == (  # pyright: ignore[reportPrivateUsage]
        "GET /login?code=…&ok=1 /?token=… HTTP/1.1"
    )


def test_the_settings_page_shows_and_saves_the_config(
    client: HTTPConnection, app: server.App, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('email = "me@acme.com"\nteams = ["core"]\n\n[github]\norg = "acme"\n')
    app.config_path = path
    body = get(client, "/settings").read().decode()
    assert "[github]" in body and "name='github:refresh_seconds'" in body
    response = post(client, "/settings", {"github:history_days": "30", ":email": "me@acme.com"})
    assert response.status == 303 and response.getheader("Location") == "/settings"
    assert "saved github.history_days" in (notice_of(response) or "")
    assert "history_days = 30" in path.read_text()
    response = get(client, "/settings", browser(client))  # without the token
    assert response.status == 401
    response.read()


def test_notifications_are_told_once(app: server.App, client: HTTPConnection) -> None:
    event = Event(kind="x", thing="pr:1", title="PR 1", text="can be merged", anchor="item-1")
    NotificationRepository(app.db).add("github", [event], datetime.now(UTC))
    response = post(client, "/notifications", {})
    [told] = json.loads(response.read())
    assert (told["title"], told["body"], told["url"]) == ("PR 1", "can be merged", "/#item-1")
    assert json.loads(post(client, "/notifications", {}).read()) == []


def test_notification_stream_delivers_events_without_a_browser_timer(
    app: server.App, client: HTTPConnection
) -> None:
    event = Event(kind="x", thing="pr:1", title="PR 1", text="can be merged")
    NotificationRepository(app.db).add("github", [event], datetime.now(UTC))
    client.request("GET", "/notifications/stream", headers=SIGNED_IN)
    response = client.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream; charset=utf-8"
    assert response.fp is not None
    assert response.fp.readline() == b": heartbeat\n"
    assert response.fp.readline() == b"\n"
    assert [n["title"] for n in json.loads(response.fp.readline()[len(b"data: ") :])] == ["PR 1"]
    assert response.fp.readline() == b"\n"
    client.close()


def test_a_notice_the_server_did_not_sign_is_not_shown(client: HTTPConnection) -> None:
    for cookie in (
        "move_forward_notice=0.run%20curl",
        "move_forward_notice=run%20curl",
        "move_forward_notice=\u00e9.run%20curl",  # not ASCII: still a page, without it
    ):
        response = get(client, "/?notice=run+curl", {**SIGNED_IN, "Cookie": cookie})
        assert response.status == 200
        assert "run curl" not in response.read().decode()


def test_a_connection_that_stalls_mid_request_is_dropped(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "REQUEST_TIMEOUT_SECONDS", 0.3)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", httpd.server_port), timeout=5) as stalled:
            stalled.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1")  # never finished
            assert stalled.recv(1024) == b""  # the server gave up and closed
    finally:
        httpd.shutdown()


def test_every_answer_forbids_framing(client: HTTPConnection) -> None:
    pages: list[tuple[str, dict[str, str]]] = [
        ("/", SIGNED_IN),  # HTML
        ("/nope", SIGNED_IN),  # an error
        ("/", {}),  # refused
        ("/style.css", {}),
    ]
    for path, headers in pages:
        response = get(client, path, headers)
        response.read()
        assert response.getheader("X-Frame-Options") == "DENY"
        assert response.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
    for response in [post(client, "/layout/reset", {})]:  # a redirect
        response.read()
        assert response.getheader("X-Frame-Options") == "DENY"
        assert response.getheader("Content-Security-Policy") == "frame-ancestors 'none'"


def test_a_failing_plugin_page_answers_500_and_the_rest_stays_up(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(req: object) -> str:
        raise RuntimeError("boom")

    def pages(self: SlackPlugin) -> dict[str, object]:
        return {"ping": boom}

    monkeypatch.setattr(SlackPlugin, "pages", pages)
    failed = get(client, "/slack/ping")
    assert failed.status == 500 and "failed" in failed.read().decode()
    assert get(client, "/").status == 200


def test_an_action_refreshes_its_plugin_and_fetches_what_it_touched_again(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    def perform(
        config: object, db: object, name: str, fields: dict[str, str], back: str | None = None
    ) -> ActionResult:
        touched = [Ref(kind="pull_request", id=pr(1).url)] if name == "slack/react" else []
        return ActionResult("done", touched=touched)

    monkeypatch.setattr(server, "perform_action", perform)
    app.run_action("slack/mark_sent", {})
    assert app._pending == {"slack"}  # pyright: ignore[reportPrivateUsage]
    app._pending = set()  # pyright: ignore[reportPrivateUsage]
    app.run_action("slack/react", {})
    assert app._pending == {"slack"}  # pyright: ignore[reportPrivateUsage]
    assert app._subjects == [Ref(kind="pull_request", id=pr(1).url)]  # pyright: ignore[reportPrivateUsage]


def test_a_plugin_page_knows_its_path(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def where(req: PageRequest[Any]) -> str:
        return f"<p>at {req.path}</p>"

    def pages(self: SlackPlugin) -> dict[str, object]:
        return {"where": where}

    monkeypatch.setattr(SlackPlugin, "pages", pages)
    assert "at /slack/where" in get(client, "/slack/where").read().decode()


def test_two_plugins_pages_of_one_name_are_each_their_own(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def pages_of(name: str) -> Callable[[object], dict[str, object]]:
        def ping(req: PageRequest[Any]) -> str:
            return f"<p>{name}'s ping</p>"

        return lambda self: {"ping": ping}

    monkeypatch.setattr(SlackPlugin, "pages", pages_of("slack"))
    monkeypatch.setattr(GitHubPlugin, "pages", pages_of("github"))
    assert "slack's ping" in get(client, "/slack/ping").read().decode()
    assert "github's ping" in get(client, "/github/ping").read().decode()
    assert get(client, "/ping").status == 404


def test_a_page_can_redirect(client: HTTPConnection, monkeypatch: pytest.MonkeyPatch) -> None:
    def pages(self: SlackPlugin) -> dict[str, object]:
        def away(req: PageRequest[Any]) -> Response:
            return Response.redirect("https://slack.com/x")

        return {"away": away}

    monkeypatch.setattr(SlackPlugin, "pages", pages)
    response = get(client, "/slack/away")
    response.read()
    assert (response.status, response.getheader("Location")) == (303, "https://slack.com/x")


def test_core_paths_are_reserved() -> None:
    paths = ["/focus", settings.PATH, server.LOGIN_PATH, server.NOTIFICATIONS_PATH, "/refresh"]
    paths += ["/layout", "/actions/x", *server.STATIC_FILES]
    assert {p.strip("/").split("/")[0] for p in paths} <= server.RESERVED_PATHS


def _post_subjects(
    client: HTTPConnection, body: str, headers: dict[str, str] | None = None
) -> HTTPResponse:
    client.request(
        "POST",
        server.SUBJECTS_PATH,
        body=body,
        headers={"Content-Type": "application/json", **SIGNED_IN, **(headers or {})},
    )
    return client.getresponse()


def test_entities_are_asked_to_be_fetched_again_through_the_endpoint(
    client: HTTPConnection, app: server.App
) -> None:
    one = json.dumps([{"kind": "pull_request", "id": pr(1).url}])
    response = _post_subjects(client, one)
    response.read()
    assert response.status == 204
    assert app._subjects == [Ref(kind="pull_request", id=pr(1).url)]  # pyright: ignore[reportPrivateUsage]
    too_many = json.dumps([{"kind": "pull_request", "id": str(n)} for n in range(51)])
    for body, headers in [
        (too_many, {}),
        ("not json", {}),
        (one, {"Origin": "https://evil.example"}),
        (one, {"Authorization": "Bearer wrong"}),
    ]:
        response = _post_subjects(client, body, headers)
        response.read()
        assert response.status in (400, 401, 403)


def test_entities_asked_for_are_fetched_before_a_refresh_comes_round(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    done: list[str] = []
    waits = [0]

    def subjects_now(refs: list[Ref]) -> set[str]:
        done.append("subjects")
        return {"jira"}  # couldn't: refreshed whole

    def refresh_now(only: set[str] | None = None) -> bool:
        done.append(f"refresh {sorted(only) if only is not None else 'all'}")
        return True

    def wait(timeout: float | None = None) -> bool:
        waits[0] += 1
        if waits[0] > 1:
            raise KeyboardInterrupt  # one round is enough
        app.request_refresh({"github"})
        app.request_refresh_subjects([Ref(kind="pull_request", id=pr(1).url)])
        return True

    monkeypatch.setattr(app, "refresh_subjects_now", subjects_now)
    monkeypatch.setattr(app, "refresh_now", refresh_now)

    def due(now: float | None = None) -> set[str]:
        return set()

    monkeypatch.setattr(app, "due", due)
    monkeypatch.setattr(app._wake, "wait", wait)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(KeyboardInterrupt):
        app.run_refresh_loop()
    assert done == ["refresh all", "subjects", "refresh ['github', 'jira']"]


def test_a_page_answering_a_bad_response_fails_rather_than_sending_it(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def injected(req: PageRequest[Any]) -> Response:
        where = req.query.get("to", [""])[0]
        return Response.redirect(where)

    def odd(req: PageRequest[Any]) -> Response:
        return Response(status=999)

    def pages(self: SlackPlugin) -> dict[str, object]:
        return {"away": injected, "odd": odd}

    monkeypatch.setattr(SlackPlugin, "pages", pages)
    response = get(client, "/slack/away?to=" + quote("/x\r\nSet-Cookie: evil=1"))
    assert response.status == 500 and response.getheader("Set-Cookie") is None
    response.read()
    response = get(client, "/slack/odd")
    assert response.status == 500
    response.read()
