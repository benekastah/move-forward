"""Slack's API: finding a person's member id by their email."""

import json
import os
import urllib.request
from urllib.parse import urlencode

from ... import https
from ...shell import CommandError, run
from .config import SlackConfig

LOOKUP_BY_EMAIL = "https://slack.com/api/users.lookupByEmail"
NOT_FOUND = "users_not_found"


def token(config: SlackConfig, email: str) -> str | None:
    """The API token from `token_env`, else the macOS keychain item for `email`; None when
    neither has one, so there are no lookups."""
    if value := os.environ.get(config.token_env):
        return value
    try:
        return (
            run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    config.keychain_service,
                    "-a",
                    email,
                    "-w",
                ]
            ).strip()
            or None
        )
    except (CommandError, FileNotFoundError):
        return None


def lookup_by_email(api_token: str, email: str) -> str | None:
    """The Slack member id of whoever has `email`; None if nobody does. Raises on any other
    answer, e.g. a token without the `users:read.email` scope."""
    url = f"{LOOKUP_BY_EMAIL}?{urlencode({'email': email})}"
    https.require_https(url, "the Slack token")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_token}"})
    with https.opener.open(request, timeout=30) as response:
        answer = json.loads(response.read())
    if answer.get("ok"):
        return str(answer["user"]["id"])
    if answer.get("error") == NOT_FOUND:
        return None
    raise RuntimeError(f"Slack's users.lookupByEmail said {answer.get('error')}")
