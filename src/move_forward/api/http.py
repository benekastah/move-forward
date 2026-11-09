"""HTTPS requests to a tool's API, refusing plain HTTP."""

from ..https import RequestError, request

__all__ = ["RequestError", "request"]
