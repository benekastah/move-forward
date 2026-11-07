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
    """Their address, lower-case; None when it has none."""
    to_me: bool
    """I'm among its `To`, not just copied."""
    bulk: bool
    """A list's, a newsletter's or an automated message (`List-Id`, `Precedence: bulk`,
    `Auto-Submitted`): not a person writing to me."""
    flagged: bool
    seen: bool
    date: datetime
    """When it was sent, in UTC."""


class MailSnapshot(Model):
    """The mailbox's unread and flagged messages, newest first."""

    messages: list[Message]
