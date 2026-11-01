import threading
from collections.abc import Callable, Iterator
from http.client import HTTPConnection, HTTPResponse
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from conftest import ME, pr, snapshot

from move_forward import server
from move_forward.config import Config
from move_forward.db import Database
from move_forward.page import Action, Item
from move_forward.panels import Layout
from move_forward.plugin import KEPT, ActionError, ActionResult
from move_forward.plugins.git.config import GitConfig
from move_forward.plugins.github.config import GitHubConfig
from move_forward.plugins.github.snapshots import PrRepository
from move_forward.plugins.slack.config import SlackConfig
from move_forward.plugins.slack.repository import SlackRepository
from move_forward.registry import migrate
from move_forward.repositories import LayoutRepository


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
    assert response.getheader("Location") == "/?" + urlencode(
        {"notice": "marked the first ping to alice as sent"}
    )
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
    assert response.status == 303 and "failed%3A+bad+ping" in (response.getheader("Location") or "")
    for target in ("alice", "robot:alice", "user:"):
        response = post(client, "/actions/slack/mark_sent", {**SENT, "target": target})
        assert "failed%3A+bad+ping" in (response.getheader("Location") or ""), target
        response.read()
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
    assert response.getheader("Location") == "/?notice=did+it"
    response = post(
        client, "/actions/delete_branch?open=act%2Cfeedback", {"repo": "/r", "branch": "b"}
    )
    assert response.getheader("Location") == "/?notice=did+it&open=act%2Cfeedback"
    assert seen == [("delete_branch", {"repo": "/r", "branch": "b"})] * 2
    assert app._wake.is_set()  # pyright: ignore[reportPrivateUsage]

    response = post(client, "/actions/boom", {})
    assert response.getheader("Location") == "/?notice=failed%3A+nope"
    body = get(client, "/?notice=failed%3A+nope").read().decode()
    assert "<span class='notice'>failed: nope</span>" in body


def test_open_parameter_is_rendered_and_kept_through_refresh(client: HTTPConnection) -> None:
    body = get(client, "/?open=github").read().decode()
    assert "<details class='group' data-key='github' data-drag open>" in body
    assert "<details class='group' data-key='act'>" in body
    response = post(client, "/refresh?open=", {})
    assert response.getheader("Location") == "/?open="
    assert " open>" not in get(client, "/?open=").read().decode()


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
    assert response.getheader("Location") == "/focus?cat=all&i=0&notice=did+it"
    # Only local paths; anything else goes to the dashboard.
    response = post(client, "/actions/x?back=//evil.example", {})
    assert response.getheader("Location") == "/?notice=did+it"
    response = post(client, "/actions/x?back=https%3A%2F%2Fevil.example", {})
    assert response.getheader("Location") == "/?notice=did+it"


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
    assert response.getheader("Location") == (
        "/focus?cat=notes.todos&i=0&kept=notes%3A7&notice=notes%2Ftodo_done+7"
    )
    body = get(client, "/focus?cat=notes.todos&i=0&kept=notes%3A7").read().decode()
    assert "<input type='checkbox' checked" in body and "x y" in body
    response = post(
        client,
        "/actions/notes/todo_undone?back=%2Ffocus%3Fcat%3Dnotes.todos%26i%3D0",
        {"todo": "7"},
    )
    assert (
        response.getheader("Location") == "/focus?cat=notes.todos&i=0&notice=notes%2Ftodo_undone+7"
    )


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
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Cookie": "move_forward_token=wrong"}):
        response = get(client, "/", headers)
        assert response.status == 401 and "mfd open" in response.read().decode()
    response = post(client, "/actions/slack/mark_sent", SENT, {"Authorization": "Bearer wrong"})
    assert response.status == 401
    response.read()
    assert SlackRepository(app.db).pings() == []


def test_static_files_need_no_token(client: HTTPConnection) -> None:
    response = get(client, "/style.css", {})
    assert response.status == 200
    response.read()


def test_login_signs_the_browser_in_with_a_cookie(client: HTTPConnection) -> None:
    response = get(client, "/login?token=wrong", {})
    assert response.status == 401 and "Set-Cookie" not in response.headers
    response.read()
    response = get(client, f"/login?token={TOKEN}", {})
    assert response.status == 303 and response.getheader("Location") == "/"
    cookie = response.getheader("Set-Cookie") or ""
    assert cookie.startswith(f"move_forward_token={TOKEN};")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    response.read()
    response = get(client, "/", {"Cookie": cookie.split(";")[0]})
    assert response.status == 200
    response.read()


def test_the_token_stays_out_of_the_log(
    client: HTTPConnection, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG", logger="move_forward.server"):
        get(client, f"/login?token={TOKEN}", {}).read()
    assert "/login?token=" in caplog.text and TOKEN not in caplog.text


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
    assert response.status == 303 and response.getheader("Location") == "/?notice=layout+reset"
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


def test_localhost_goes_to_127_0_0_1_where_the_sign_in_cookie_is(client: HTTPConnection) -> None:
    host = f"localhost:{client.port}"
    response = get(client, "/focus?cat=all&i=2", {"Host": host})
    assert response.status == 302
    assert response.getheader("Location") == f"http://127.0.0.1:{client.port}/focus?cat=all&i=2"
    response.read()
    response = get(client, "//evil.example/x", {"Host": host})
    assert response.getheader("Location", "").startswith(f"http://127.0.0.1:{client.port}/")
    response.read()


def test_the_loop_refreshes_each_plugin_as_it_falls_due(
    app: server.App, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    app.config = app.config.model_copy(
        update={"git": GitConfig(repos=[]), "refresh_on_load_seconds": 120}
    )
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
