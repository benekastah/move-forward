"""`mfd open`'s side of signing a browser in (the server's is in `server` and `auth`).

The token goes nowhere: the client proves it has it by an HMAC over a nonce the server just
made, and only believes the server, and so only opens a browser at it, once the server has
proved the same over the client's own nonce. A program that grabbed the port first can do
neither, and learns nothing it could use.
"""

import http.client
import json
import secrets
from http import HTTPStatus
from typing import cast

from . import auth

TIMEOUT_SECONDS = 5


class SignInError(Exception):
    """Why the browser can't be signed in; says what to do about it."""


def _post(
    connection: http.client.HTTPConnection, port: int, path: str, body: dict[str, str] | None
) -> tuple[HTTPStatus, dict[str, str]]:
    connection.request(
        "POST",
        path,
        body=json.dumps(body or {}),
        headers={"Host": f"{auth.HOST}:{port}", "Content-Type": "application/json"},
    )
    response = connection.getresponse()
    data = response.read()
    try:
        parsed = cast(object, json.loads(data))
    except ValueError:
        parsed = None
    found = cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
    return HTTPStatus(response.status), {k: v for k, v in found.items() if isinstance(v, str)}


def login_code(port: int, token: str) -> str:
    """A login code from the dashboard on `port`, once it has shown it knows `token`.
    Raises `SignInError` when it isn't running, isn't this dashboard, or won't sign in."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
    try:
        status, challenge = _post(connection, port, "/login/challenge", None)
        nonce = challenge.get("nonce")
        if status != HTTPStatus.OK or not isinstance(nonce, str):
            raise SignInError(f"the server on port {port} isn't the dashboard, or is busy")
        mine = secrets.token_urlsafe(32)
        status, answer = _post(
            connection,
            port,
            "/login/code",
            {
                "nonce": nonce,
                "client_nonce": mine,
                "proof": auth.proof(token, "client", nonce, mine),
            },
        )
        code, theirs = answer.get("code"), answer.get("proof")
        if status == HTTPStatus.UNAUTHORIZED:
            raise SignInError(
                f"the dashboard on port {port} doesn't know this token: is it using another"
                " database? (`mfd serve` restarts to pick up a new token by itself)"
            )
        if status != HTTPStatus.OK or not isinstance(code, str) or not isinstance(theirs, str):
            raise SignInError(f"the server on port {port} isn't the dashboard")
        if not auth.matches(auth.proof(token, "server", mine, code), theirs):
            raise SignInError(f"the server on port {port} didn't prove it is the dashboard")
        return code
    except (OSError, http.client.HTTPException) as e:  # refused, timeout
        raise SignInError(
            f"couldn't reach the dashboard on port {port} ({e}): is `mfd serve` running?"
        ) from e
    finally:
        connection.close()
