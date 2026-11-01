"""Slack's Web API: signing in (OAuth with PKCE, as a public client: no client secret), and
the methods slack calls with the user token it gets."""

import json
import urllib.request
from typing import Any
from urllib.parse import urlencode

from ... import https

API = "https://slack.com/api/"
AUTHORIZE = "https://slack.com/oauth/v2/authorize"
"""Where the browser goes to sign in to the app."""
NOT_FOUND = "users_not_found"
TIMEOUT_SECONDS = 30


class SlackError(RuntimeError):
    """Slack answered `"ok": false`; `error` is its code, e.g. `invalid_auth`."""

    def __init__(self, method: str, error: str):
        super().__init__(f"Slack's {method} said {error}")
        self.error = error


def call(method: str, api_token: str | None, params: dict[str, str]) -> dict[str, Any]:
    """POST `params` (form-encoded) to Slack's `method`, with `api_token` if given, and return
    its answer; raises SlackError when it isn't `ok`."""
    url = API + method
    https.require_https(url, "the Slack token")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    request = urllib.request.Request(
        url, data=urlencode(params).encode(), headers=headers, method="POST"
    )
    with https.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        answer: dict[str, Any] = json.loads(response.read())
    if not answer.get("ok"):
        raise SlackError(method, str(answer.get("error", "an error")))
    return answer


def lookup_by_email(api_token: str, email: str) -> str | None:
    """The Slack member id of whoever has `email`; None if nobody does. Raises on any other
    answer, e.g. a token without the `users:read.email` scope."""
    try:
        answer = call("users.lookupByEmail", api_token, {"email": email})
    except SlackError as e:
        if e.error == NOT_FOUND:
            return None
        raise
    return str(answer["user"]["id"])
