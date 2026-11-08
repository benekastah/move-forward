"""The entity kinds mail publishes, with their attributes; see `docs/entities.md`."""

from ...api import KindModel
from ...api.kinds import Person

THREAD = "mail.thread"


class ThreadAttrs(KindModel):
    """A `mail.thread`: a conversation with unread or flagged mail, by the Message-ID it
    started with; it points at the `person` who wrote its latest message."""

    subject: str
    sender_email: str | None
    """Who its latest message's `From` says wrote it, lower-case; None when it gives no usable
    address. Unchecked: the thread points at a `person` only when the sender was verified."""
    unread: int
    """How many of its messages are unread."""
    flagged: bool
    """It's flagged, as `panels.Thread.flagged` counts: by whoever wrote its latest message."""
    bulk: bool
    """Its latest message is a list's, a newsletter's or automated."""
    to_me: bool
    """Its latest message has me among its `To`, not just copied."""
    latest: str
    """When the server received its latest message, an ISO datetime."""


class MailPerson(Person):
    """A `person`: someone whose mail is in the mailbox, by `mail/<address>`; `handle` and
    `email` are their address."""
