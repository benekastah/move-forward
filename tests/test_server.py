import json
import socket
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPResponse
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote, urlencode

import pytest
from conftest import ME, pr, snapshot

from move_forward import auth, registry, server
from move_forward.config import Config
from move_forward.db import Database
from move_forward.graph import Graph
from move_forward.notifications import Event, NotificationRepository
from move_forward.page import Action, Item
from move_forward.panels import Layout
from move_forward.plugin import KEPT, ActionError, ActionResult, Context, PageHandler
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.plugin import GitHubPlugin
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.plugin import SlackPlugin
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.registry import migrate
from move_forward.repositories import LayoutRepository, SessionRepository


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> server.App:
    config = Config(
        email="me@acme.com",
        github=GitHubConfig(org="acme"),
        teams=["core"],
        slack=SlackConfig(),
        db_path=tmp_path / "x.db",
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


def _results(run: FakeRun) -> Callable[[object, object, str, dict[str, str]], ActionResult]:
    """An action runner standing in for `perform_action`, from one that returns notices."""

    def perform(config: object, db: object, name: str, fields: dict[str, str]) -> ActionResult:
        return ActionResult(run(config, db, name, fields))

    return perform


def test_dashboard_page_renders_panels_and_ping_links(client: HTTPConnection) -> None:
    response = get(client, "/")
    body = response.read().decode()
    assert response.status == 200
    assert "Needs a reviewer" in body
    assert f"as {ME}" in body
    assert "/ping?pr=" + quote(pr(1).url, safe="") in body


def test_stylesheet_and_404(client: HTTPConnection) -> None:
    assert get(client, "/style.css").status == 200
    favicon = get(client, "/favicon.svg")
    assert favicon.status == 200 and favicon.getheader("Content-Type") == "image/svg+xml"
    assert favicon.read().startswith(b"<svg")
    assert b"<circle" not in get(client, "/logo.svg").read()
    assert get(client, "/nope").status == 404


def test_ping_page_lists_targets(client: HTTPConnection) -> None:
    response = get(client, "/ping?pr=" + quote(pr(1).url, safe=""))
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
    body = get(client, "/ping?pr=" + quote(pr(1).url, safe="")).read().decode()
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


def test_page_load_refreshes_stale_data_in_the_background(
    client: HTTPConnection, app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seconds since boot: a machine that just started has a small monotonic clock.
    monkeypatch.setattr(server.time, "monotonic", lambda: 5.0)
    get(client, "/").read()
    assert app._wake.is_set()  # pyright: ignore[reportPrivateUsage]
    app.refresh_now()
    app._wake.clear()  # pyright: ignore[reportPrivateUsage]
    body = get(client, "/").read().decode()
    assert not app._wake.is_set()  # pyright: ignore[reportPrivateUsage]
    assert "data-reload='60'" in body


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
    assert "What to focus on?" in body and "href='/focus?cat=all&amp;i=0'" in body
    body = get(client, "/focus?cat=all&i=0").read().decode()
    assert "1 of 1" in body and pr(1).title in body
    assert get(client, "/focus?cat=all&i=x").status == 200
    assert get(client, "/focus?cat=nope").status == 404


def test_actions_from_a_focus_page_redirect_back_to_it(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_: object) -> str:
        return "did it"

    monkeypatch.setattr(server, "perform_action", _results(fake_run))
    response = post(client, "/actions/x?back=%2Ffocus%3Fcat%3Dall%26i%3D0", {})
    assert (response.getheader("Location"), notice_of(response)) == ("/focus?cat=all&i=0", "did it")
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


def test_ticking_a_todo_off_redirects_back_with_it(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_perform(
        _config: object, _db: object, name: str, fields: dict[str, str]
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

    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeServer)
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


def test_a_request_without_the_token_is_refused(client: HTTPConnection, app: server.App) -> None:
    wrong_session = f"{auth.session_cookie(client.port)}=wrong"
    for headers in (
        browser(client),
        browser(client, Authorization="Bearer wrong"),
        browser(client, Cookie=wrong_session),
    ):
        response = get(client, "/", headers)
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
        response = get(client, "/focus?cat=all&i=2", {"Host": host})
        assert response.status == 302
        assert response.getheader("Location") == f"{canonical}/focus?cat=all&i=2"
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
    app.config = app.config.model_copy(
        update={"git": GitConfig(repos=[], refresh_seconds=60), "refresh_on_load_seconds": 120}
    )
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
    app.refresh_if_stale()  # github's data is 301s old, over refresh_on_load_seconds
    assert app._pending == {"github"}  # pyright: ignore[reportPrivateUsage]


class _GitHubWithChecks(GitHubPlugin):
    """github whose change check answers from `answers`, raising for an exception."""

    change_check_seconds: ClassVar[int | None] = 60
    answers: ClassVar[list[bool | Exception]] = []

    def changed(self, ctx: Context[GitHubConfig], graph: Graph) -> bool:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


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
    # A refresh asks once more, to forget what it changed itself, and starts the wait over.
    monkeypatch.setattr(_GitHubWithChecks, "answers", [True, True])
    monkeypatch.setattr(server.time, "monotonic", lambda: 2000.0)
    app.refresh_now({"github"})
    assert _GitHubWithChecks.answers == [True]
    assert app.check_for_changes(2030.0) == set() and _GitHubWithChecks.answers == [True]
    assert app.check_for_changes(2060.0) == {"github"}


class _GitHubWatchingGit(_GitHubWithChecks):
    change_check_seconds: ClassVar[int | None] = None
    watches: ClassVar[list[str]] = ["git"]


def test_a_plugin_watching_another_is_asked_right_after_that_one_refreshes(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubWatchingGit()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    app.config = app.config.model_copy(update={"git": GitConfig(repos=[])})
    migrate(app.config, app.db)
    monkeypatch.setattr(_GitHubWatchingGit, "answers", [True, False, False])
    app.refresh_now({"git"})
    assert app._pending == {"github"}  # pyright: ignore[reportPrivateUsage]
    app._pending = set()  # pyright: ignore[reportPrivateUsage]
    app.refresh_now({"github"})  # its own refresh: asked only to forget what it did
    app.refresh_now({"git"})  # nothing of github's moved
    assert app._pending == set() and _GitHubWatchingGit.answers == []  # pyright: ignore[reportPrivateUsage]
    assert app.check_for_changes() == set()  # no change_check_seconds: never polled


def test_secrets_in_query_parameters_are_left_out_of_the_log() -> None:
    line = "GET /slack/callback?code=abc&state=xyz&ok=1 /login?token=t0k HTTP/1.1"
    assert server._redact(line) == (  # pyright: ignore[reportPrivateUsage]
        "GET /slack/callback?code=…&state=…&ok=1 /login?token=… HTTP/1.1"
    )


class _GitHubWithASignIn(GitHubPlugin):
    sign_in_pages: ClassVar[frozenset[str]] = frozenset({"/github/back"})

    def pages(self) -> dict[str, PageHandler]:
        def page(ctx: Context[GitHubConfig], graph: Graph, query: dict[str, list[str]]) -> str:
            return f"back with {query.get('state', ['?'])[0]}"

        return {"/github/back": page, "/github/private": page}


def test_a_sign_in_page_is_served_without_the_cookie_and_the_rest_still_need_it(
    client: HTTPConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap = {"github": _GitHubWithASignIn()}
    monkeypatch.setattr(registry, "PLUGINS", [swap.get(p.name, p) for p in registry.PLUGINS])
    response = get(
        client, "/github/back?state=s1", browser(client)
    )  # as a redirect from Slack comes
    assert response.status == 200 and response.read().decode() == "back with s1"
    response = get(client, "/github/private?state=s1", browser(client))
    assert response.status == 401
    response.read()


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


def test_a_notice_the_server_did_not_sign_is_not_shown(client: HTTPConnection) -> None:
    for cookie in ("move_forward_notice=0.run%20curl", "move_forward_notice=run%20curl"):
        body = get(client, "/?notice=run+curl", {**SIGNED_IN, "Cookie": cookie}).read().decode()
        assert "run curl" not in body


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
    def boom(ctx: object, graph: object, query: object) -> str:
        raise RuntimeError("boom")

    def pages(self: SlackPlugin) -> dict[str, object]:
        return {"/ping": boom}

    monkeypatch.setattr(SlackPlugin, "pages", pages)
    failed = get(client, "/ping")
    assert failed.status == 500 and "failed" in failed.read().decode()
    assert get(client, "/").status == 200
