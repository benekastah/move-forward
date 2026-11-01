"""Sending a secret to a web API: over https only, and never on to wherever a redirect
points, since urllib would forward the `Authorization` header with it."""

import urllib.request
from email.message import Message
from typing import IO
from urllib.parse import urlsplit


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise RuntimeError(f"{req.full_url} redirected to {newurl}; not following it")


opener = urllib.request.build_opener(_NoRedirects)
"""Opens requests carrying a secret: redirects raise instead of being followed."""


def require_https(url: str, secret: str) -> None:
    """Refuse to send `secret` (e.g. "the Jira token") anywhere but https."""
    if urlsplit(url).scheme != "https":
        raise RuntimeError(f"not sending {secret} to {url}: it isn't https")
