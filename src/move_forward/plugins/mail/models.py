"""The mail plugin's data: the unread and flagged messages of a mailbox, headers only."""

from datetime import datetime

from ...api import Model

MAX_SUBJECT = 300
"""The longest subject kept; past it, an ellipsis."""


class Message(Model):
    """A message, as far as ranking it needs: never its body."""

    uid: int
    """Its IMAP UID in the mailbox."""
    message_id: str | None
    """Its Message-ID, without `<>`; None when it has none."""
    thread: str
    """The Message-ID its conversation started with (the first of `References`, else
    `In-Reply-To`, else its own), or `uid:<uid>` for one with none."""
    subject: str
    sender_name: str
    """Who it's from, by name, else by address."""
    sender_email: str | None
    """Their address, lower-case, as `From` says (unchecked: see `verified`); None when it
    gives none that can be shown as one address."""
    to_me: bool
    """I'm among its `To`, not just copied."""
    bulk: bool
    """A list's, a newsletter's or an automated message (`List-Id`, `Precedence: bulk`,
    `Auto-Submitted`): not a person writing to me."""
    flagged: bool
    important: bool = False
    """Gmail labels it Important; never from another server."""
    replies_to_me: bool = False
    """It answers mail I sent (its `In-Reply-To`, else the last of its `References`, names one):
    the newest `max_messages` I sent within `since_days`."""
    seen: bool
    verified: bool
    """The receiving server found its `From` authentic (DMARC passed), so its sender is who it
    says. `From` is otherwise whatever the sender wrote."""
    date: datetime
    """When the server received it (`INTERNALDATE`), in UTC; the sender's `Date` only when the
    server doesn't say."""


class MailSnapshot(Model):
    """The mailbox's unread and flagged messages, newest first."""

    messages: list[Message]
