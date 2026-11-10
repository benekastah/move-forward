"""Tags as core names them: `handle` is the tag core makes of a label or a typed tag (`Garbage
Day` → `garbage-day`), for a plugin matching a `tag` entity to labels of its own, as mail finds
Gmail's own labels (`\\Inbox`) among a thread's tags to leave them out of what it's about."""

from ..tags import handle

__all__ = ["handle"]
