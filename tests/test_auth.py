"""The server's token file, and `mfd open` signing the browser in with it."""

import json
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from move_forward import auth, cli
from move_forward.act_now import Feedback
from move_forward.config import Config
from move_forward.db import Database
from move_forward.private import is_private
from move_forward.registry import migrate
from move_forward.repositories import FeedbackRepository, SessionRepository
from move_forward.server import App, make_handler


def test_the_token_is_made_once_and_only_i_can_read_it(tmp_path: Path) -> None:
    path = auth.token_path(tmp_path / "data" / "dashboard.db")
    token = auth.load_or_create(path)
    assert len(token) >= 40 and is_private(path)
    assert auth.load_or_create(path) == token


def test_a_token_file_others_can_read_is_made_private(tmp_path: Path) -> None:
    path = tmp_path / auth.TOKEN_FILE
    path.write_text("abc\n")
    path.chmod(0o644)
    assert auth.load_or_create(path) == "abc"
    assert is_private(path)


def test_an_empty_token_file_gets_a_new_token(tmp_path: Path) -> None:
    path = tmp_path / auth.TOKEN_FILE
    path.write_text("")
    assert auth.load_or_create(path)
    assert is_private(path)


def test_a_request_carries_the_token_as_a_bearer_header_and_a_session_as_a_cookie() -> None:
    assert auth.bearer("Bearer abc") == "abc"
    assert auth.bearer("Basic abc") is None and auth.bearer(None) is None
    name = auth.session_cookie(8765)
    assert auth.session_from(f"x=1; {name}=abc", 8765) == "abc"
    # Other apps' cookies on this host, however malformed, don't hide ours.
    assert auth.session_from(f"k(=1; {name}=abc", 8765) == "abc"
    assert auth.session_from(f'a=1; b; x="open; {name}=abc', 8765) == "abc"
    assert auth.session_from(f"{name}=abc", 9000) is None  # another dashboard's
    assert auth.session_from(None, 8765) is None
    assert auth.matches("abc", "abc") and not auth.matches("abc", "abd")
    assert not auth.matches("abc", None)


def test_sessions_are_kept_hashed_expire_and_can_all_be_revoked() -> None:
    db = Database(":memory:")
    sessions = SessionRepository(db)
    now = datetime(2026, 11, 1, tzinfo=UTC)
    first = sessions.create(now, timedelta(days=1))
    second = sessions.create(now, timedelta(days=400))
    assert sessions.valid(first, now) and sessions.valid(second, now)
    assert not sessions.valid("made up", now)
    assert first not in str(db.query("SELECT * FROM sessions"))  # only its hash is kept
    later = now + timedelta(days=2)
    assert not sessions.valid(first, later) and sessions.valid(second, later)
    sessions.prune(later)
    assert len(db.query("SELECT * FROM sessions")) == 1
    assert sessions.revoke_all() == 1 and not sessions.valid(second, now)


def test_nonces_and_codes_are_single_use_short_lived_and_bounded() -> None:
    clock = [0.0]
    handshake = auth.Handshake(lambda: clock[0])
    nonce = handshake.nonce() or ""
    assert handshake.take_nonce(nonce) and not handshake.take_nonce(nonce)  # once
    assert not handshake.take_nonce("made up")
    stale = handshake.nonce() or ""
    clock[0] += auth.NONCE_SECONDS + 1
    assert not handshake.take_nonce(stale)
    code = handshake.code() or ""
    clock[0] += auth.CODE_SECONDS - 1
    assert handshake.take_code(code)
    late = handshake.code() or ""
    clock[0] += auth.CODE_SECONDS + 1
    assert not handshake.take_code(late)
    issued = [handshake.nonce() for _ in range(auth.HANDSHAKE_LIMIT + 5)]
    assert issued.count(None) >= 5  # a flood can't grow the server's memory


def _server(tmp_path: Path, token: str) -> tuple[ThreadingHTTPServer, int]:
    config = Config(email="me@x", db_path=tmp_path / "dashboard.db")
    db = Database(":memory:")
    migrate(config, db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(config, db, token)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_port


def _config(tmp_path: Path, port: int) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        f'email = "me@x"\nport = {port}\ndb_path = "{(tmp_path / "dashboard.db").as_posix()}"\n'
    )
    return config


def test_mfd_open_proves_the_server_knows_the_token_then_opens_a_login_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = auth.load_or_create(auth.token_path(tmp_path / "dashboard.db"))
    httpd, port = _server(tmp_path, token)
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    try:
        assert cli.main(["-c", str(_config(tmp_path, port)), "open"]) == 0
    finally:
        httpd.shutdown()
    [url] = opened
    assert url.startswith(f"http://mfd.localhost:{port}/login?code=") and token not in url


def test_mfd_open_sends_no_token_to_a_server_that_cannot_prove_it_has_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = auth.load_or_create(auth.token_path(tmp_path / "dashboard.db"))
    heard: list[bytes] = []

    class Squatter(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            heard.append(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            heard.append(str(self.headers).encode())
            answer = (
                {"nonce": "n"} if self.path.endswith("challenge") else {"code": "c", "proof": "x"}
            )
            data = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            pass

    squatting = ThreadingHTTPServer(("127.0.0.1", 0), Squatter)
    threading.Thread(target=squatting.serve_forever, daemon=True).start()
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    try:
        config = _config(tmp_path, squatting.server_port)
        assert cli.main(["-c", str(config), "open"]) == 1
    finally:
        squatting.shutdown()
    assert opened == [] and "didn't prove" in capsys.readouterr().err
    assert token.encode() not in b"".join(heard)  # it never saw the token


def test_mfd_open_says_when_nothing_is_listening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    auth.load_or_create(auth.token_path(tmp_path / "dashboard.db"))
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    assert cli.main(["-c", str(_config(tmp_path, port)), "open"]) == 1
    assert "is `mfd serve` running?" in capsys.readouterr().err


def test_a_rotated_token_signs_everyone_out_and_a_running_server_takes_it_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = auth.token_path(tmp_path / "dashboard.db")
    old = auth.load_or_create(path)
    file = auth.TokenFile(path)
    assert file.get() == old
    config = _config(tmp_path, 8765)
    session = SessionRepository(Database(tmp_path / "dashboard.db")).create(
        datetime.now(UTC), timedelta(days=1)
    )
    assert cli.main(["-c", str(config), "token", "rotate"]) == 0
    assert "ended 1 session(s)" in capsys.readouterr().out
    new = path.read_text().strip()
    assert new != old and is_private(path)
    assert file.get() == new  # read again, without restarting the server
    assert not SessionRepository(Database(tmp_path / "dashboard.db")).valid(
        session, datetime.now(UTC)
    )


def test_mfd_snoozes_lists_the_snoozes_still_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "dashboard.db"
    config = tmp_path / "config.toml"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n')
    assert cli.main(["-c", str(config), "snoozes"]) == 0  # creates and migrates the database
    assert json.loads(capsys.readouterr().out) == []
    now = datetime.now(UTC)
    feedback = FeedbackRepository(Database(db_path))
    for id, until in (("over", now - timedelta(hours=1)), ("running", now + timedelta(days=2))):
        feedback.say(
            ("pr", id, "Review"), Feedback(action="snooze", fingerprint="f1", until=until), now
        )
    feedback.say(("pr", "pinned", "Review"), Feedback(action="pin", fingerprint="f1"), now)
    assert cli.main(["-c", str(config), "snoozes"]) == 0
    [snooze] = json.loads(capsys.readouterr().out)
    assert snooze | {"until": ""} == {
        "kind": "pr",
        "id": "running",
        "verb": "Review",
        "until": "",
        "fingerprint": "f1",
    }


def test_two_processes_making_the_token_at_once_agree(tmp_path: Path) -> None:
    path = auth.token_path(tmp_path / "dashboard.db")
    tokens: list[str] = []
    threads = [
        threading.Thread(target=lambda: tokens.append(auth.load_or_create(path))) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(tokens)) == 1 and tokens[0] == path.read_text().strip()
    assert [p.name for p in tmp_path.iterdir()] == ["token"]  # no drafts left behind


def test_mfd_logout_ends_every_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "dashboard.db"
    config = tmp_path / "config.toml"
    config.write_text(f'email = "me@x"\ndb_path = "{db_path.as_posix()}"\n')
    assert cli.main(["-c", str(config), "logout"]) == 0  # creates and migrates the database
    assert capsys.readouterr().out == "ended 0 session(s)\n"
    session = SessionRepository(Database(db_path)).create(datetime.now(UTC), timedelta(days=1))
    assert cli.main(["-c", str(config), "logout"]) == 0
    assert capsys.readouterr().out == "ended 1 session(s)\n"
    assert not SessionRepository(Database(db_path)).valid(session, datetime.now(UTC))
