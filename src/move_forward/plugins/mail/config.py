"""`[mail]`, and the mailboxes it names."""

import re
from dataclasses import dataclass
from typing import Any, Self, cast

from pydantic import Field, field_validator, model_validator

from ...api import Strict

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
_ACCOUNT = re.compile(r"[a-z][a-z0-9_]{0,29}")


@dataclass(frozen=True)
class Mailbox:
    """One mailbox to read, as `[mail]` and its account (if any) say together."""

    name: str
    """The account's name (`work` for `[mail.work]`); empty for `[mail]`'s own."""
    host: str
    port: int
    username: str
    mailbox: str
    sent_mailbox: str
    message_url: str
    auth_servers: list[str] | None
    token_env: str
    keychain_service: str
    since_days: int
    max_messages: int
    known_days: int

    def trusted_auth_servers(self) -> frozenset[str]:
        """`auth_servers`, or those known for `host`, lower-case."""
        names = (
            self.auth_servers
            if self.auth_servers is not None
            else (KNOWN_AUTH_SERVERS.get(self.host.lower(), []))
        )
        return frozenset(name.lower() for name in names)

    @property
    def shown(self) -> str:
        """How the page names it: its mailbox, with its account's name when it has one."""
        return f"{self.name}'s {self.mailbox}" if self.name else self.mailbox


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


class MailAccount(Strict):
    """`[mail.<name>]`: one of several mailboxes. Each key is as `[mail]`'s, and one it leaves
    out is `[mail]`'s, but for its password (`token_env`, `keychain_service`): its own."""

    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    mailbox: str | None = None
    sent_mailbox: str | None = None
    message_url: str | None = None
    auth_servers: list[str] | None = None
    token_env: str | None = None
    """`MAIL_PASSWORD_<NAME>` unless set (`MAIL_PASSWORD_WORK` for `[mail.work]`)."""
    keychain_service: str | None = None
    """`move-forward-mail-<name>` unless set; `mfd token set mail.<name>` stores it."""

    @field_validator("host")
    @classmethod
    def _valid_host(cls, value: str | None) -> str | None:
        return value if value is None else _host(value)

    @field_validator("message_url")
    @classmethod
    def _valid_message_url(cls, value: str | None) -> str | None:
        return value if value is None else _message_url(value)


class MailConfig(Strict):
    """`[mail]`: mailboxes read over IMAP, for their unread and flagged mail. Optional.

    One mailbox is `[mail]`'s own `host` and `username`; several are its accounts, each a table
    of its own (`[mail.home]`, `[mail.work]`), taking from `[mail]` what they leave out. It signs
    in to each with its password (for Gmail, an app password) from `token_env`, else the
    system's secret store, over TLS only. It reads them without changing them: only the headers
    it needs, never a message's body, and nothing is marked read.
    """

    host: str = ""
    """The IMAP server, e.g. `imap.gmail.com`: needed here unless every account says its own."""
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    """Its port for IMAP over TLS (IMAPS)."""
    username: str = ""
    """Who to sign in as, usually my address; also my address for telling mail sent to me from
    mail I'm copied on. Needed here unless every account says its own."""
    mailbox: str = DEFAULT_MAILBOX
    """The mailbox to read."""
    sent_mailbox: str = ""
    """Where my sent mail is, to know which mail answers me; empty, the mailbox the server
    marks as sent (`[Gmail]/Sent Mail`, `Sent`). Only its Message-IDs and recipients are read."""
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
    sender is verified. With accounts, only those on this `host` (whatever its case) without
    their own take them, and the config is refused when none does."""
    token_env: str = DEFAULT_MAIL_TOKEN_ENV
    """The environment variable holding `[mail]`'s own password; an account has its own."""
    keychain_service: str = DEFAULT_MAIL_KEYCHAIN_SERVICE
    """The service `[mail]`'s own password is stored under in the system's secret store (the
    macOS keychain, Windows' Credential Manager, the desktop keyring), when `token_env` isn't
    set; its account is the top-level `email`. An account has its own."""
    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=60)
    """How often the dashboard reads the mailboxes in the background; it also looks every couple
    of minutes for a change, and reads them at once if there is one."""
    known_days: int = Field(default=DEFAULT_KNOWN_DAYS, ge=1)
    """How far back my sent mail is read: someone I wrote to at least twice in it is someone I
    know, whose mail counts as important; mail answering any of it replies to me."""
    urgent_words: list[str] = Field(default_factory=list[str])
    """Words or phrases in a subject that make the conversation critical, above other important
    mail, e.g. "outage", "action required": whole words, ignoring case. Only from someone I
    know, verified, as anyone can write them."""
    low_words: list[str] = Field(default_factory=list[str])
    """Words or phrases in a subject that rank the conversation with lists' mail, e.g.
    "digest", "FYI": whole words, ignoring case; not a reply to me or mail from someone I know,
    and flagged mail still counts."""
    since_days: int = Field(default=DEFAULT_SINCE_DAYS, ge=1)
    """Mail older than this is left out, flagged or not."""
    max_messages: int = Field(default=DEFAULT_MAX_MESSAGES, ge=1, le=2000)
    """The most unread messages read in each mailbox, newest first; flagged ones are read
    however many."""
    accounts: dict[str, MailAccount] = Field(
        default_factory=dict[str, MailAccount], json_schema_extra={"tables": True}
    )
    """The accounts, by name: each `[mail.<name>]` table. Not a key to write itself."""

    @model_validator(mode="before")
    @classmethod
    def _gather_accounts(cls, data: Any) -> Any:
        """Each sub-table of `[mail]` is an account."""
        if not isinstance(data, dict):
            return data
        section = cast(dict[str, Any], data)
        tables: dict[str, Any] = {k: v for k, v in section.items() if isinstance(v, dict)}
        for name in tables:
            if not _ACCOUNT.fullmatch(name):
                raise ValueError(
                    f"[mail.{name}]: an account's name is lower-case letters, digits and _"
                )
        if not tables:
            return section
        return {**{k: v for k, v in section.items() if k not in tables}, "accounts": tables}

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if (
            self.accounts
            and self.auth_servers is not None
            and not any(self._takes_auth_servers(a) for a in self.accounts.values())
        ):
            # They're for [mail]'s host: an account on another keeps to its own.
            raise ValueError(
                "[mail] sets auth_servers, but no account is on [mail]'s host to take them: set"
                " them in the accounts they're for"
            )
        if self.accounts and self.username:
            # Its own mailbox would stop being read, unnoticed, once accounts come.
            raise ValueError(
                "[mail] has accounts, so its username names no mailbox: move it to an account"
                " of its own ([mail.home]), with `mfd token set mail.home` for its password"
            )
        self.mailboxes()  # each has a host and a username
        return self

    def mailboxes(self) -> list[Mailbox]:
        """The mailboxes to read: each account's, or `[mail]`'s own when it has none. Raises
        ValueError for one without a host or a username."""
        if not self.accounts:
            return [self._mailbox("", MailAccount())]
        return [self._mailbox(name, account) for name, account in self.accounts.items()]

    def mailbox_named(self, name: str) -> Mailbox | None:
        """The mailbox called `name` (`""` for `[mail]`'s own); None when there's none."""
        return next((m for m in self.mailboxes() if m.name == name), None)

    @field_validator("host")
    @classmethod
    def _valid_host(cls, value: str) -> str:
        return _host(value)

    @field_validator("message_url")
    @classmethod
    def _valid_message_url(cls, value: str) -> str:
        return _message_url(value)

    def _takes_auth_servers(self, account: MailAccount) -> bool:
        """Whether `account` trusts `[mail]`'s `auth_servers`: it sets none of its own, and it's on
        `[mail]`'s host (whose results headers they are), whatever its case."""
        host = account.host if account.host is not None else self.host
        return account.auth_servers is None and bool(host) and host.lower() == self.host.lower()

    def _mailbox(self, name: str, account: MailAccount) -> Mailbox:
        def said[T](mine: T | None, shared: T) -> T:
            return mine if mine is not None else shared

        host, username = said(account.host, self.host), said(account.username, self.username)
        if not host or not username:
            raise ValueError(
                f"[mail.{name}] needs a host and a username, its own or [mail]'s"
                if name
                else "[mail] needs a host and a username, or accounts ([mail.<name>]) that have them"
            )
        return Mailbox(
            name=name,
            host=host,
            port=said(account.port, self.port),
            username=username,
            mailbox=said(account.mailbox, self.mailbox),
            sent_mailbox=said(account.sent_mailbox, self.sent_mailbox),
            message_url=said(account.message_url, self.message_url),
            auth_servers=self.auth_servers
            if self._takes_auth_servers(account)
            else account.auth_servers,
            # A password is never shared: an account's isn't [mail]'s.
            token_env=account.token_env or f"{DEFAULT_MAIL_TOKEN_ENV}_{name.upper()}"
            if name
            else self.token_env,
            keychain_service=account.keychain_service or f"{DEFAULT_MAIL_KEYCHAIN_SERVICE}-{name}"
            if name
            else self.keychain_service,
            since_days=self.since_days,
            max_messages=self.max_messages,
            known_days=self.known_days,
        )
