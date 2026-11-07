"""Reading a mailbox over IMAP (the standard library's `imaplib`), headers only, read-only."""

import imaplib
import re
import ssl
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from email.parser import BytesHeaderParser
from email.policy import default
from email.utils import getaddresses, parsedate_to_datetime

from ...api import SourceError, keychain
from .config import MailConfig
from .models import MAX_SUBJECT, MailSnapshot, Message

TIMEOUT_SECONDS = 30
HEADERS = (
    "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES LIST-ID PRECEDENCE AUTO-SUBMITTED"
)
"""The headers fetched: who, what, when, the conversation, and whether a person wrote it."""
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_UID = re.compile(rb"\bUID (\d+)")
_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
_MESSAGE_ID = re.compile(r"<([^<>\s]+)>")
_STATUS = re.compile(rb"\((.*)\)")


def password(config: MailConfig, email: str) -> str:
    """The password from the configured env var, else the system's secret store under
    `keychain_service` for `email`."""
    if value := keychain.token(config, email):
        return value
    raise SourceError(f"no mail password: run `mfd token set mail`, or set ${config.token_env}")


def _quoted(mailbox: str) -> str:
    """`mailbox` as an IMAP quoted string."""
    return '"' + mailbox.replace("\\", "\\\\").replace('"', '\\"') + '"'


@contextmanager
def _signed_in(config: MailConfig, secret: str) -> Generator[imaplib.IMAP4]:
    """A session over TLS, with the server's certificate checked, signed in."""
    imap = imaplib.IMAP4_SSL(
        config.host, config.port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_SECONDS
    )
    try:
        try:
            imap.login(config.username, secret)
        except imaplib.IMAP4.error as err:
            raise SourceError(
                f"{config.host} refused to sign in {config.username} ({err}); for Gmail, the "
                "password must be an app password"
            ) from err
        yield imap
    finally:
        with suppress(imaplib.IMAP4.error, OSError):
            imap.logout()


def _ok(answer: tuple[str, Sequence[object]], what: str) -> Sequence[object]:
    """What an IMAP command answered; raises unless it answered OK."""
    typ, data = answer
    if typ != "OK":
        raise RuntimeError(f"IMAP {what} failed: {data!r}")
    return data


def imap_date(day: datetime) -> str:
    """`day` as IMAP's dates are written, e.g. `7-Nov-2026`, whatever the locale."""
    return f"{day.day}-{_MONTHS[day.month - 1]}-{day.year}"


def _fetched(data: Sequence[object]) -> Iterator[tuple[bytes, bytes]]:
    """Each message's response line (with whatever followed its headers, where some servers
    put `FLAGS`) and its headers."""
    pending: tuple[bytes, bytes] | None = None
    for part in data:
        if isinstance(part, tuple) and len(part) == 2:  # pyright: ignore[reportUnknownArgumentType]
            if pending:
                yield pending
            meta, headers = part  # pyright: ignore[reportUnknownVariableType]
            pending = (bytes(meta), bytes(headers))  # pyright: ignore[reportUnknownArgumentType]
        elif isinstance(part, bytes) and pending:
            pending = (pending[0] + part, pending[1])
    if pending:
        yield pending


def collect(config: MailConfig, email: str, now: datetime) -> MailSnapshot:
    """The mailbox's unread and flagged messages since `since_days` ago, newest first, at most
    `max_messages`. Opened read-only, and the headers fetched with `BODY.PEEK`, so nothing is
    marked read."""
    secret = password(config, email)
    with _signed_in(config, secret) as imap:
        _ok(imap.select(_quoted(config.mailbox), readonly=True), "EXAMINE")
        since = imap_date(now - timedelta(days=config.since_days))
        [found] = _ok(imap.uid("SEARCH", "SINCE", since, "OR", "UNSEEN", "FLAGGED"), "SEARCH")
        uids = (found if isinstance(found, bytes) else b"").split()[-config.max_messages :]
        if not uids:
            return MailSnapshot(messages=[])
        fetched = _ok(
            imap.uid(
                "FETCH",
                b",".join(uids).decode(),
                f"(UID FLAGS BODY.PEEK[HEADER.FIELDS ({HEADERS})])",
            ),
            "FETCH",
        )
    me = {a.lower() for a in (config.username, email) if "@" in a}
    messages = [m for meta, headers in _fetched(fetched) if (m := message(meta, headers, me))]
    return MailSnapshot(messages=sorted(messages, key=lambda m: (m.date, m.uid), reverse=True))


def status(config: MailConfig, email: str) -> str:
    """What changes when mail comes, goes or is read: the mailbox's message count, next UID,
    UID validity and unread count, as the server says them."""
    with _signed_in(config, password(config, email)) as imap:
        [line] = _ok(
            imap.status(_quoted(config.mailbox), "(MESSAGES UIDNEXT UIDVALIDITY UNSEEN)"),
            "STATUS",
        )
    found = _STATUS.search(line if isinstance(line, bytes) else b"")
    return found.group(1).decode() if found else ""


def _message_ids(text: str) -> list[str]:
    return _MESSAGE_ID.findall(text)


def message(meta: bytes, raw: bytes, me: set[str]) -> Message | None:
    """The message a FETCH response describes (its line and its headers), or None when the
    server left out its UID. `me` are my addresses, lower-case."""
    uid = _UID.search(meta)
    if uid is None:
        return None
    flags = _FLAGS.search(meta)
    flag_set = set(flags.group(1).decode().split()) if flags else set[str]()
    headers = BytesHeaderParser(policy=default).parsebytes(raw)

    def header(name: str) -> str:
        try:
            return str(headers.get(name) or "")
        except (ValueError, IndexError, TypeError):  # a malformed header
            return ""

    [(name, address), *_] = getaddresses([header("From")]) or [("", "")]
    to = {a.lower() for _, a in getaddresses([header("To")])}
    own = next(iter(_message_ids(header("Message-ID"))), None)
    thread = next(
        iter(_message_ids(header("References")) + _message_ids(header("In-Reply-To"))), own
    )
    try:
        sent = parsedate_to_datetime(header("Date"))
        sent = sent.astimezone(UTC) if sent.tzinfo else sent.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        sent = datetime(1970, 1, 1, tzinfo=UTC)
    subject = " ".join(header("Subject").split()) or "(no subject)"
    if len(subject) > MAX_SUBJECT:
        subject = subject[: MAX_SUBJECT - 1] + "…"
    auto = header("Auto-Submitted").strip().lower()
    return Message(
        uid=int(uid.group(1)),
        message_id=own,
        thread=thread or f"uid:{uid.group(1).decode()}",
        subject=subject,
        sender_name=name or address or "someone",
        sender_email=address.lower() if "@" in address else None,
        to_me=bool(to & me),
        bulk=bool(header("List-Id"))
        or header("Precedence").strip().lower() in ("bulk", "list", "junk")
        or (bool(auto) and auto != "no"),
        flagged="\\Flagged" in flag_set,
        seen="\\Seen" in flag_set,
        date=sent,
    )
