"""`[mail]`."""

import re

from pydantic import Field, field_validator

from ...api import Strict

DEFAULT_REFRESH_SECONDS = 900
DEFAULT_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_SINCE_DAYS = 14
DEFAULT_MAX_MESSAGES = 200
DEFAULT_MAIL_TOKEN_ENV = "MAIL_PASSWORD"
DEFAULT_MAIL_KEYCHAIN_SERVICE = "move-forward-mail"
GMAIL_MESSAGE_URL = "https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}"
KNOWN_AUTH_SERVERS = {"imap.gmail.com": ["mx.google.com"]}
"""What servers whose names are known stamp their `Authentication-Results` with, by IMAP host."""
APP_SCHEMES = ("message:", "mid:")
"""Schemes of links that open a mail app (Apple Mail's `message:`, `mid:`), which the page
reaches through the plugin's `open` page, since it links only to http(s) itself."""
_HOST = re.compile(r"[A-Za-z0-9.-]+")
_PLACEHOLDER = re.compile(r"\{([^}]*)\}")


class MailConfig(Strict):
    """`[mail]`: a mailbox read over IMAP, for its unread and flagged mail. Optional.

    It signs in as `username` with the password (for Gmail, an app password) from
    `token_env`, else the system's secret store, over TLS only. It reads the mailbox without
    changing it: only the headers it needs, never a message's body, and nothing is marked read.
    """

    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=60)
    """How often the dashboard reads the mailbox in the background; it also looks every couple
    of minutes for a change, and reads it at once if there is one."""
    host: str
    """The IMAP server, e.g. `imap.gmail.com`."""
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    """Its port for IMAP over TLS (IMAPS)."""
    username: str
    """Who to sign in as, usually my address; also my address for telling mail sent to me from
    mail I'm copied on."""
    mailbox: str = DEFAULT_MAILBOX
    """The mailbox to read."""
    since_days: int = Field(default=DEFAULT_SINCE_DAYS, ge=1)
    """Mail older than this is left out, flagged or not."""
    max_messages: int = Field(default=DEFAULT_MAX_MESSAGES, ge=1, le=2000)
    """The most messages read, newest first."""
    message_url: str = ""
    """A link to open a message, with `{message_id}` (its Message-ID, without `<>`) and
    `{username}` filled in: for Gmail,
    `https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}`; for Apple
    Mail, `message:%3C{message_id}%3E`; for Thunderbird, `mid:{message_id}`. An `https://`
    link, or one of those schemes. Empty means mail isn't linked."""
    auth_servers: list[str] | None = None
    """The names my mail server stamps its `Authentication-Results` with (their first word,
    the authserv-id), e.g. `mx.google.com`: a sender counts as who `From` says only when that
    server found it authentic (DMARC passed), since anyone can write any `From`. Unset, it's
    known for Gmail; for another server, look for the name in a message's source. Empty, no
    sender is verified."""
    token_env: str = DEFAULT_MAIL_TOKEN_ENV
    """The environment variable holding the password."""
    keychain_service: str = DEFAULT_MAIL_KEYCHAIN_SERVICE
    """The service the password is stored under in the system's secret store (the macOS
    keychain, Windows' Credential Manager, the desktop keyring), when `token_env` isn't set;
    its account is the top-level `email`."""

    def trusted_auth_servers(self) -> frozenset[str]:
        """`auth_servers`, or those known for `host`, lower-case."""
        names = (
            self.auth_servers
            if self.auth_servers is not None
            else (KNOWN_AUTH_SERVERS.get(self.host.lower(), []))
        )
        return frozenset(name.lower() for name in names)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        if not _HOST.fullmatch(value):
            raise ValueError(
                f"{value!r} isn't a host name, e.g. imap.gmail.com (no scheme or path)"
            )
        return value

    @field_validator("message_url")
    @classmethod
    def _message_url(cls, value: str) -> str:
        if value and not value.startswith(("https://", *APP_SCHEMES)):
            raise ValueError(
                f"{value!r} must be an https:// link, or start with {' or '.join(APP_SCHEMES)}"
            )
        if unknown := set(_PLACEHOLDER.findall(value)) - {"message_id", "username"}:
            raise ValueError(
                f"{value!r} names {', '.join(sorted(unknown))}: only {{message_id}} "
                "and {username} are filled in"
            )
        return value
