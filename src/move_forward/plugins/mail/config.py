"""`[mail]`: the mailbox it reads."""

import re
from typing import Annotated, Any, ClassVar, Self, cast

from pydantic import Field, field_validator, model_validator

from move_forward.api import LOCKED
from move_forward.api.keychain import TokenFields

DEFAULT_REFRESH_SECONDS = 900
DEFAULT_PORT = 993
DEFAULT_MAILBOX = "INBOX"
DEFAULT_SINCE_DAYS = 14
DEFAULT_KNOWN_DAYS = 90
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


def _host(value: str) -> str:
    if value and not _HOST.fullmatch(value):
        raise ValueError(f"{value!r} isn't a host name, e.g. imap.gmail.com (no scheme or path)")
    return value


def _message_url(value: str) -> str:
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


class MailConfig(TokenFields):
    """`[mail]`: a mailbox read over IMAP, for its unread and flagged mail. Optional.

    It signs in with its password (for Gmail, an app password) from `token_env`, else the
    system's secret store, over TLS only, and reads the mailbox without changing it: only the
    headers it needs, never a message's body, and nothing is marked read.
    """

    default_token_env: ClassVar[str] = DEFAULT_MAIL_TOKEN_ENV
    default_keychain_service: ClassVar[str] = DEFAULT_MAIL_KEYCHAIN_SERVICE

    # Locked, with `port`: where its password is sent.
    host: Annotated[str, LOCKED] = ""
    """The IMAP server, e.g. `imap.gmail.com`."""
    port: Annotated[int, LOCKED] = Field(default=DEFAULT_PORT, ge=1, le=65535)
    """Its port for IMAP over TLS (IMAPS)."""
    username: str = ""
    """Who to sign in as, usually my address; also my address for telling mail sent to me from
    mail I'm copied on."""
    mailbox: str = DEFAULT_MAILBOX
    """The mailbox to read."""
    sent_mailbox: str = ""
    """Where my sent mail is, to know which mail answers me; empty, the mailbox the server
    marks as sent (`[Gmail]/Sent Mail`, `Sent`). Only its Message-IDs and recipients are read."""
    # Locked: where its items link, with my username in the link.
    message_url: Annotated[str, LOCKED] = ""
    """A link to open a message, with `{message_id}` (its Message-ID, without `<>`) and
    `{username}` filled in: for Gmail,
    `https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}`; for Apple
    Mail, `message:%3C{message_id}%3E`; for Thunderbird, `mid:{message_id}`; empty, mail
    isn't linked. An `https://` link, or one of those schemes."""
    # Locked: whose word decides a sender is who they claim.
    auth_servers: Annotated[list[str] | None, LOCKED] = None
    """The names my mail server stamps its `Authentication-Results` with: unset for Gmail
    (`mx.google.com`, known), else the name a received message's `Authentication-Results`
    header starts with (its authserv-id), e.g. `["mx.work.example"]`. A sender counts as who
    `From` says only when that server found it authentic (DMARC passed), since anyone can
    write any `From`. Empty, or unset for a server other than Gmail's, no sender is
    verified."""
    token_env: str = ""
    """The environment variable holding the password. `MAIL_PASSWORD` unless set."""
    keychain_service: str = ""
    """The service the password is stored under, by `mfd token set mail`, in the system's
    secret store (the macOS keychain, Windows' Credential Manager, the desktop keyring), when
    `token_env` isn't set; its account is the top-level `email`. `move-forward-mail` unless
    set."""
    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=60)
    """How often the dashboard reads the mailbox in the background; it also looks every couple
    of minutes for a change, and reads it at once if there is one."""
    known_days: int = Field(default=DEFAULT_KNOWN_DAYS, ge=1)
    """How far back my sent mail is read: someone I wrote to at least twice in it is someone I
    know, whose mail counts as important; mail answering any of it replies to me."""
    urgent_words: list[str] = Field(default_factory=list[str])
    """Words or phrases in a subject that make the conversation critical, above other important
    mail, when it's from someone I know, verified, e.g. "outage", "action required": whole
    words, ignoring case. Mail from others doesn't count, as anyone can write them."""
    low_words: list[str] = Field(default_factory=list[str])
    """Words or phrases in a subject that rank the conversation with lists' mail, e.g.
    "digest", "FYI": whole words, ignoring case; not a reply to me or mail from someone I know,
    and flagged mail still counts."""
    since_days: int = Field(default=DEFAULT_SINCE_DAYS, ge=1)
    """Mail older than this is left out, flagged or not."""
    max_messages: int = Field(default=DEFAULT_MAX_MESSAGES, ge=1, le=2000)
    """The most unread messages read, newest first; flagged ones are read however many."""

    @model_validator(mode="before")
    @classmethod
    def _no_accounts(cls, data: Any) -> Any:
        """A config from when `[mail.<name>]` tables were accounts, each a mailbox, is refused
        saying how to keep one, rather than as an unknown key."""
        if not isinstance(data, dict):
            return data
        section = cast(dict[str, Any], data)
        tables = [k for k, v in section.items() if isinstance(v, dict)]
        if tables:
            listed = " and ".join(f"[mail.{name}]" for name in tables)
            raise ValueError(
                f"[mail] reads one mailbox, so {listed} can't be accounts of their own: move the"
                " keys of the one to read into [mail], and delete the tables; its password was"
                " stored under `move-forward-mail-<name>` (set `keychain_service` to it, or run"
                " `mfd token set mail`), or in $MAIL_PASSWORD_<NAME> (set `token_env` to it)"
            )
        return section

    @field_validator("host")
    @classmethod
    def _valid_host(cls, value: str) -> str:
        return _host(value)

    @field_validator("message_url")
    @classmethod
    def _valid_message_url(cls, value: str) -> str:
        return _message_url(value)

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if not (self.host and self.username):
            raise ValueError("[mail] needs a host and a username")
        return self

    def trusted_auth_servers(self) -> frozenset[str]:
        """`auth_servers`, or those known for `host`, lower-case."""
        names = (
            self.auth_servers
            if self.auth_servers is not None
            else KNOWN_AUTH_SERVERS.get(self.host.lower(), [])
        )
        return frozenset(name.lower() for name in names)
