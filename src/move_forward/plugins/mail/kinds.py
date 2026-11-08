"""The entity kinds mail publishes, with their attributes; see `docs/entities.md`."""

from ...api import KindModel
from ...api.kinds import Person

THREAD = "mail.thread"


class ThreadAttrs(KindModel):
    """A `mail.thread`: a conversation with unread or flagged mail, by the Message-ID it
    started with; it points at the `person` who wrote its latest message."""

    subject: str
    sender_email: str | None
    """Who wrote its latest message, lower-case; None when they gave no address."""
    unread: int
    """How many of its messages are unread."""
    flagged: bool
    """One of its messages is flagged."""
    bulk: bool
    """Its latest message is a list's, a newsletter's or automated."""
    to_me: bool
    """Its latest message has me among its `To`, not just copied."""
    latest: str
    """When its latest message was sent, an ISO datetime."""


class MailPerson(Person):
    """A `person`: someone whose mail is in the mailbox, by `mail/<address>`; `handle` and
    `email` are their address."""
