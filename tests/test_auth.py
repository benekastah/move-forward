"""The server's token file, and `mfd open` signing the browser in with it."""

import threading
from pathlib import Path

import pytest

from move_forward import auth, cli
from move_forward.private import is_private


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


def test_a_request_carries_the_token_as_a_bearer_header_or_a_cookie() -> None:
    assert auth.from_request("Bearer abc", None) == "abc"
    assert auth.from_request(None, "x=1; move_forward_token=abc") == "abc"
    # Other apps' cookies on localhost, however malformed, don't hide ours.
    assert auth.from_request(None, "k(=1; move_forward_token=abc") == "abc"
    assert auth.from_request(None, 'a=1; b; x="open; move_forward_token=abc') == "abc"
    assert auth.from_request("Basic abc", None) is None
    assert auth.from_request(None, None) is None
    assert auth.matches("abc", "abc") and not auth.matches("abc", "abd")
    assert not auth.matches("abc", None)


def test_mfd_open_opens_the_login_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'email = "me@x"\nport = 9999\ndb_path = "{(tmp_path / "dashboard.db").as_posix()}"\n'
    )
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(cli.webbrowser, "open", fake_open)
    assert cli.main(["-c", str(config), "open"]) == 0
    token = (tmp_path / auth.TOKEN_FILE).read_text().strip()
    assert opened == [f"http://127.0.0.1:9999/login?token={token}"]


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
