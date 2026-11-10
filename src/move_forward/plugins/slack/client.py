"""Slack's Web API: the methods slack calls, with the token given it."""

import json
from typing import Any

from ...api import Model, http

API = "https://slack.com/api/"
NOT_FOUND = "users_not_found"
TIMEOUT_SECONDS = 30


class Channel(Model):
    """A channel visible to the token, with text that may identify its GitHub team."""

    id: str
    name: str
    topic: str = ""
    purpose: str = ""


def channels(api_token: str) -> list[Channel]:
    """List active public and accessible private channels, following every cursor.
    Requires channels:read and groups:read; never reads messages or joins channels."""
    found: dict[str, Channel] = {}
    cursor = ""
    seen: set[str] = set()
    while True:
        answer = call(
            "conversations.list",
            api_token,
            {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": "200",
                "cursor": cursor,
            },
        )
        for channel in answer.get("channels", []):
            if not channel.get("is_archived"):
                item = Channel(
                    id=channel["id"],
                    name=channel["name"],
                    topic=channel.get("topic", {}).get("value", ""),
                    purpose=channel.get("purpose", {}).get("value", ""),
                )
                found[item.id] = item
        cursor = answer.get("response_metadata", {}).get("next_cursor", "").strip()
        if not cursor:
            return list(found.values())
        if cursor in seen:
            raise ValueError("Slack repeated a channel-list cursor")
        seen.add(cursor)


class SlackError(RuntimeError):
    """Slack answered `"ok": false`; `error` is its code, e.g. `invalid_auth`."""

    def __init__(self, method: str, error: str):
        super().__init__(f"Slack's {method} said {error}")
        self.error = error


def call(method: str, api_token: str, params: dict[str, str]) -> dict[str, Any]:
    """POST `params` (form-encoded) to Slack's `method`, with `api_token`, and return
    its answer; raises SlackError when it isn't `ok`, `http.RequestError` when Slack can't
    be reached, and ValueError for an answer that isn't JSON."""
    body = http.request(
        API + method,
        secret_name="the Slack token",
        headers={"Authorization": f"Bearer {api_token}"},
        form=params,
        timeout=TIMEOUT_SECONDS,
    )
    answer: dict[str, Any] = json.loads(body)
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
