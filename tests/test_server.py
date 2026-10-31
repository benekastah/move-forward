import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.client import HTTPConnection, HTTPResponse
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from conftest import ME, pr, snapshot

from move_forward_dashboard import server
from move_forward_dashboard.config import Config, TeamConfig
from move_forward_dashboard.db import Database
from move_forward_dashboard.repositories import PingRepository, SnapshotRepository


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> server.App:
    config = Config(
        org="acme",
        teams=[TeamConfig(github_slug="core")],
        repos=[],
        notes_dir=tmp_path,
        db_path=tmp_path / "x.db",
    )
    db = Database(":memory:")
    SnapshotRepository(db).save(
        "github",
        datetime.now(UTC),
        snapshot(mine=[pr(1, requested_users=["alice"], requested_teams=["web"])]),
    )
    refreshed: list[bool] = []

    def fake_refresh(*_: object) -> None:
        refreshed.append(True)

    monkeypatch.setattr(server, "refresh", fake_refresh)
    instance = server.App(config, db)
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


def get(client: HTTPConnection, path: str) -> HTTPResponse:
    client.request("GET", path)
    return client.getresponse()


def post(client: HTTPConnection, path: str, form: dict[str, str]) -> HTTPResponse:
    body = urlencode(form)
    client.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return client.getresponse()


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
        "/pings",
        {"pr_url": pr(1).url, "target": "user:alice", "kind": "first", "message": "hi <there>"},
    )
    assert response.status == 303 and response.getheader("Location") == "/"
    pings = PingRepository(app.db).all()
    assert [(p.target, p.kind, p.message) for p in pings] == [("user:alice", "first", "hi <there>")]
    # The next page shows the follow-up.
    body = get(client, "/ping?pr=" + quote(pr(1).url, safe="")).read().decode()
    assert "Gentle bump" in body


def test_invalid_ping_form_is_rejected(client: HTTPConnection) -> None:
    assert post(client, "/pings", {"pr_url": "x", "target": "t", "kind": "later"}).status == 400


def test_page_load_refreshes_stale_data_in_the_background(
    client: HTTPConnection, app: server.App
) -> None:
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

    def fake_run(name: str, _config: object, fields: dict[str, str]) -> str:
        seen.append((name, fields))
        if name == "boom":
            raise server.actions.ActionError("nope")
        return "did it"

    monkeypatch.setattr(server.actions, "run", fake_run)
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
    body = get(client, "/?open=my_prs").read().decode()
    assert "<details class='group' data-key='my_prs' open>" in body
    assert "<details class='group' data-key='act'>" in body
    response = post(client, "/refresh?open=", {})
    assert response.getheader("Location") == "/?open="
    assert " open>" not in get(client, "/?open=").read().decode()


def test_refresh_wakes_the_loop(client: HTTPConnection, app: server.App) -> None:
    assert post(client, "/refresh", {}).status == 303
    assert app.refresh_now() is True
    assert app.refreshed == [True]  # type: ignore[attr-defined]


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

    monkeypatch.setattr(server.actions, "run", fake_run)
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
    def fake_run(name: str, _config: object, fields: dict[str, str]) -> str:
        return f"{name} {fields['text']}"

    monkeypatch.setattr(server.actions, "run", fake_run)
    response = post(
        client,
        "/actions/todo_done?back=%2Ffocus%3Fcat%3Dtodos%26i%3D0",
        {"entry": "APP-1", "text": "x y"},
    )
    assert response.getheader("Location") == (
        "/focus?cat=todos&i=0&done_entry=APP-1&done_text=x+y&notice=todo_done+x+y"
    )
    body = get(client, "/focus?cat=todos&i=0&done_entry=APP-1&done_text=x+y").read().decode()
    assert "<input type='checkbox' checked" in body and "x y" in body
    response = post(
        client,
        "/actions/todo_undone?back=%2Ffocus%3Fcat%3Dtodos%26i%3D0",
        {"entry": "APP-1", "text": "x y"},
    )
    assert response.getheader("Location") == "/focus?cat=todos&i=0&notice=todo_undone+x+y"


def test_ctrl_c_shuts_down_quietly(
    app: server.App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    closed: list[bool] = []

    class FakeServer:
        server_port = 1

        def __init__(self, *_: object) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(server.shell, "_stopping", threading.Event())  # don't stop other tests

    def no_start(_self: object) -> None:
        pass  # no refresh thread; the fake server never waits for it anyway

    monkeypatch.setattr(server.threading.Thread, "start", no_start)
    with caplog.at_level("INFO"):
        server.serve(app.config, app.db, 1)
    assert closed == [True] and "shutting down on ctrl-c" in caplog.text
    assert server.shell.stopping()
    with pytest.raises(server.shell.CommandError, match="shutting down"):
        server.shell.run(["true"])
