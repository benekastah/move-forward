"""`api.http.request`: what it sends, and the one error it raises when there's no answer."""

import http.client
import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from move_forward import https
from move_forward.https import RequestError, request


class _Opener:
    """Stands in for `https.opener.open`: answers with `answer`, or raises it; keeps what
    was sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, answer: bytes | Exception) -> None:
        self.answer = answer
        self.sent: list[tuple[urllib.request.Request, float]] = []
        monkeypatch.setattr(https.opener, "open", self.open)

    def open(self, req: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.sent.append((req, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return io.BytesIO(self.answer)


def test_a_json_body_is_posted_with_its_type_and_the_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _Opener(monkeypatch, b'{"ok": true}')
    answer = request(
        "https://tool/api",
        secret_name="the token",
        headers={"Authorization": "Bearer t"},
        json={"a": [1]},
        timeout=7,
    )
    assert answer == b'{"ok": true}'
    [(sent, timeout)] = opener.sent
    assert sent.get_method() == "POST" and timeout == 7
    assert sent.data == json.dumps({"a": [1]}).encode()
    assert sent.get_header("Content-type") == "application/json"
    assert sent.get_header("Authorization") == "Bearer t"


def test_form_fields_are_posted_url_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Opener(monkeypatch, b"")
    request("https://tool/api", secret_name="the token", form={"email": "a+b@x"})
    [(sent, _)] = opener.sent
    assert sent.get_method() == "POST" and sent.data == b"email=a%2Bb%40x"
    assert sent.get_header("Content-type") == "application/x-www-form-urlencoded"


def test_no_body_is_a_get_unless_another_method_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Opener(monkeypatch, b"")
    request("https://tool/api", secret_name="the token")
    request("https://tool/api", secret_name="the token", method="DELETE")
    assert [(r.get_method(), r.data) for r, _ in opener.sent] == [("GET", None), ("DELETE", None)]


def test_nothing_is_sent_but_over_https(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Opener(monkeypatch, b"")
    with pytest.raises(RuntimeError, match="not sending the token to http://tool/api"):
        request("http://tool/api", secret_name="the token")
    assert opener.sent == []


def test_json_and_form_together_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Opener(monkeypatch, b"")
    with pytest.raises(ValueError, match="not both"):
        request("https://tool/api", secret_name="the token", json={}, form={})
    assert opener.sent == []


def test_an_error_status_keeps_its_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _Opener(
        monkeypatch,
        urllib.error.HTTPError("https://tool/api", 401, "Unauthorized", Message(), None),
    )
    with pytest.raises(RequestError, match="https://tool/api answered HTTP 401 Unauthorized") as e:
        request("https://tool/api", secret_name="the token")
    assert e.value.status == 401 and e.value.reason == "Unauthorized"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (urllib.error.URLError("no network"), "no network"),
        (TimeoutError("timed out"), "timed out"),
        (ConnectionResetError("reset"), "reset"),
        (http.client.IncompleteRead(b"{"), "IncompleteRead(1 bytes read)"),
    ],
)
def test_no_answer_is_one_error_without_a_status(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, reason: str
) -> None:
    _Opener(monkeypatch, failure)
    with pytest.raises(RequestError, match="couldn't reach https://tool/api") as e:
        request("https://tool/api", secret_name="the token")
    assert e.value.status is None and e.value.reason == reason
    assert e.value.__cause__ is failure
