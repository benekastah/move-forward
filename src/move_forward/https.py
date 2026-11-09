"""Sending a secret to a web API: over https only, and never on to wherever a redirect
points, since urllib would forward the `Authorization` header with it."""

import http.client
import json as jsonlib
import urllib.error
import urllib.request
from collections.abc import Mapping
from email.message import Message
from typing import IO
from urllib.parse import urlencode, urlsplit, urlunsplit


def _shown_url(url: str) -> str:
    """`url` as an error may say it: without its query or userinfo, where a tool's API might
    take a key, so a failure never writes one to the log, the page or a notification."""
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]  # its port and an IPv6 address's brackets too
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


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
        raise RuntimeError(
            f"{_shown_url(req.full_url)} redirected to {_shown_url(newurl)}; not following it"
        )


opener = urllib.request.build_opener(_NoRedirects)
"""Opens requests carrying a secret: redirects raise instead of being followed."""


def require_https(url: str, secret: str) -> None:
    """Refuse to send `secret` (e.g. "the Jira token") anywhere but https."""
    if urlsplit(url).scheme != "https":
        raise RuntimeError(f"not sending {secret} to {_shown_url(url)}: it isn't https")


class RequestError(Exception):
    """A request that got no answer, or an HTTP error status for one: no network, a refused
    or cut connection, a timeout, a garbled response, or the tool's 4xx or 5xx. Its message
    names the URL without its query or userinfo; a secret goes in a header or the body, never
    the URL."""

    def __init__(self, url: str, reason: str, status: int | None = None):
        shown = _shown_url(url)
        super().__init__(
            f"{shown} answered HTTP {status} {reason}".rstrip()
            if status is not None
            else f"couldn't reach {shown}: {reason}"
        )
        self.url = url
        self.reason = reason
        """Why, as urllib says it: the status's reason phrase, or the connection's error."""
        self.status = status
        """The HTTP status the tool answered with (e.g. 401), or None when it didn't answer."""


def request(
    url: str,
    *,
    secret_name: str,
    headers: Mapping[str, str] | None = None,
    json: object = None,
    form: Mapping[str, str] | None = None,
    method: str | None = None,
    timeout: float = 30,
) -> bytes:
    """The body of the answer to a request to `url`, which carries a secret (`secret_name`
    says which, e.g. "the Jira token", for `require_https`) in its `headers`.

    `json` (an object to encode, None for no body) or `form` (fields to url-encode) is sent
    as the body, with its `Content-Type`; there may be one or neither. `method` defaults to
    POST with a body and GET without. It goes through `opener`, so a redirect raises
    RuntimeError, as does a URL that isn't https. Raises `RequestError` when the request
    gets no answer within `timeout` seconds, or one with an HTTP error status (its
    `status`)."""
    if json is not None and form is not None:
        raise ValueError("a request sends json or form fields, not both")
    require_https(url, secret_name)
    sent = dict(headers or {})
    data: bytes | None = None
    if json is not None:
        data = jsonlib.dumps(json).encode()
        sent["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode()
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=sent, method=method)
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise RequestError(url, str(e.reason), e.code) from e
    except urllib.error.URLError as e:
        raise RequestError(url, str(e.reason)) from e
    # A timeout reading the answer, a reset connection (OSErrors), or a cut or garbled one.
    except (OSError, http.client.HTTPException) as e:
        raise RequestError(url, str(e) or type(e).__name__) from e
