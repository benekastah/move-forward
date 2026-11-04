"""HTTPS requests to a tool's API, refusing plain HTTP."""

from ..https import opener, require_https

__all__ = ["opener", "require_https"]
