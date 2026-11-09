"""HTTPS requests to a tool's API, refusing plain HTTP."""

from ..https import RequestError, opener, request, require_https

__all__ = ["RequestError", "opener", "request", "require_https"]
