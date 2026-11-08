"""Reading a mailbox over IMAP (the standard library's `imaplib`), headers only, read-only."""

import imaplib
import re
import ssl
import unicodedata
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
FETCH_CHUNK = 500
"""The most messages fetched by one command."""
HEADERS = (
    "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES LIST-ID PRECEDENCE AUTO-SUBMITTED"
    " AUTHENTICATION-RESULTS"
)
"""The headers fetched: who, what, when, the conversation, whether a person wrote it, and
whether the receiving server found `From` authentic."""
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_UID = re.compile(rb"\bUID (\d+)")
_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
_MESSAGE_ID = re.compile(r"<([^<>\s]+)>")
_STATUS = re.compile(rb"\((.*)\)")
_INTERNALDATE = re.compile(
    rb'\bINTERNALDATE "\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)"'
)
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_HIDDEN = {
    chr(c) for c in (0x061C, 0x200B, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))
}
"""Characters that change how text reads without showing: bidi controls, and the zero-width
space. Other format characters (ZWJ, ZWNJ) are kept: emoji and scripts need them."""


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
        except UnicodeEncodeError as err:
            raise SourceError(
                "IMAP signs in in ASCII: the username and password can't have other characters"
            ) from err
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
    """The mailbox's unread and flagged messages since `since_days` ago, newest first: at most
    `max_messages` unread, and every flagged one. Opened read-only, and the headers fetched
    with `BODY.PEEK`, so nothing is marked read."""
    secret = password(config, email)
    with _signed_in(config, secret) as imap:
        try:
            _ok(imap.select(_quoted(config.mailbox), readonly=True), "EXAMINE")
        except UnicodeEncodeError as err:
            raise SourceError(f"mailbox {config.mailbox!r}: only ASCII names work") from err
        since = imap_date(now - timedelta(days=config.since_days))
        # Unread mail up to the cap, newest first, and flagged mail however much there is:
        # flagged is what I marked, which a busy week of newsletters mustn't push out.
        [unread] = _ok(imap.uid("SEARCH", "SINCE", since, "UNSEEN"), "SEARCH")
        [flagged] = _ok(imap.uid("SEARCH", "SINCE", since, "FLAGGED"), "SEARCH")
        newest = (unread if isinstance(unread, bytes) else b"").split()[-config.max_messages :]
        marked = (flagged if isinstance(flagged, bytes) else b"").split()
        uids = sorted(set(newest) | set(marked), key=int)
        if not uids:
            return MailSnapshot(messages=[])
        # In chunks: flagged mail has no cap, and a server limits a command's length.
        fetched: list[object] = []
        for start in range(0, len(uids), FETCH_CHUNK):
            fetched += _ok(
                imap.uid(
                    "FETCH",
                    b",".join(uids[start : start + FETCH_CHUNK]).decode(),
                    f"(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS ({HEADERS})])",
                ),
                "FETCH",
            )
    me = {a.lower() for a in (config.username, email) if "@" in a}
    trusted = config.trusted_auth_servers()
    messages = [
        m for meta, headers in _fetched(fetched) if (m := message(meta, headers, me, trusted))
    ]
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


def _received(meta: bytes) -> datetime | None:
    """When the server got the message (`INTERNALDATE`), in UTC; None when it didn't say."""
    found = _INTERNALDATE.search(meta)
    if found is None:
        return None
    day, month, year, hour, minute, second, sign, off_h, off_m = (
        g.decode() for g in found.groups()
    )
    if month.title() not in _MONTHS:
        return None
    offset = timedelta(hours=int(off_h), minutes=int(off_m)) * (-1 if sign == "-" else 1)
    try:
        local = datetime(
            int(year), _MONTHS.index(month.title()) + 1, int(day),
            int(hour), int(minute), int(second), tzinfo=UTC,
        )  # fmt: skip
        return local - offset
    except (ValueError, OverflowError):
        return None


def _sent(date: str) -> datetime | None:
    """The `Date` header in UTC; None when it can't be read."""
    try:
        sent = parsedate_to_datetime(date)
        return sent.astimezone(UTC) if sent.tzinfo else sent.replace(tzinfo=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _shown(text: str) -> str:
    """`text` without control characters or those in `_HIDDEN`, which could make a subject or
    name read as something else, and its whitespace collapsed."""
    return " ".join(
        "".join(" " if unicodedata.category(c) == "Cc" or c in _HIDDEN else c for c in text).split()
    )


_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)  # fmt: skip
"""Unicode's default-ignorable code points: drawn as nothing, whatever their category."""


def _invisible(c: str) -> bool:
    return unicodedata.category(c) in ("Cc", "Cf", "Zs", "Zl", "Zp") or any(
        low <= ord(c) <= high for low, high in _IGNORABLE
    )


def _address(text: str) -> str | None:
    """`text`, lower-cased, if it can be shown as one address: one `@`, and nothing hidden or
    spaced in it (control, format, space or default-ignorable characters: a soft hyphen, a
    word joiner or a variation selector would make it read as another). None otherwise."""
    if text.count("@") != 1 or any(_invisible(c) for c in text):
        return None
    return text.lower()


def _name(text: str) -> str:
    """A sender's name as shown: not one with an `@` in it, which could pass for an address
    (`Boss <boss@acme.com>`) while the real one is elsewhere."""
    shown = _shown(text)
    return "" if "@" in shown else shown


def _without_comments(text: str) -> str:
    """`text` with its quoted strings and (possibly nested) comments blanked out: in
    `Authentication-Results` they hold what the sender chose, e.g. their envelope address."""
    text = _QUOTED.sub('""', text)
    out: list[str] = []
    depth = 0
    escaped = False
    for c in text:
        if escaped:
            escaped = False
        elif c == "\\" and depth:
            escaped = True
        elif c == "(":
            depth += 1
        elif c == ")" and depth:
            depth -= 1
        elif not depth:
            out.append(c)
    return "".join(out)


def dmarc_passed(results: str, trusted: frozenset[str], from_domain: str) -> bool:
    """Whether an `Authentication-Results` header, stamped by one of the `trusted` servers,
    says DMARC passed for `from_domain` (RFC 8601: the authserv-id, then `;`-separated
    `method=result` with properties such as `header.from=`). Its one `dmarc` result must be
    that pass: a second means someone else's text got in, so neither counts."""
    authserv, *found = _without_comments(results).split(";")
    if not authserv.split() or authserv.split()[0].lower() not in trusted:
        return False
    dmarc: list[list[str]] = []
    for result in found:
        method, _, rest = result.strip().partition("=")
        if method.strip().lower() == "dmarc":
            dmarc.append(rest.split())
    if len(dmarc) != 1 or not dmarc[0] or dmarc[0][0].lower() != "pass":
        return False
    properties = {key.lower(): value for key, _, value in (w.partition("=") for w in dmarc[0][1:])}
    return properties.get("header.from", "").lower() == from_domain


def message(
    meta: bytes, raw: bytes, me: set[str], trusted: frozenset[str] = frozenset()
) -> Message | None:
    """The message a FETCH response describes (its line and its headers), or None when the
    server left out its UID. `me` are my addresses, lower-case; `trusted` the servers whose
    `Authentication-Results` count (`MailConfig.auth_servers`)."""
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
    email = _address(address)
    to = {a.lower() for _, a in getaddresses([header("To")])}
    own = next(iter(_message_ids(header("Message-ID"))), None)
    thread = next(
        iter(_message_ids(header("References")) + _message_ids(header("In-Reply-To"))), own
    )
    # The server's clock, not the sender's: `Date` says whatever the sender likes.
    date = _received(meta) or _sent(header("Date")) or datetime(1970, 1, 1, tzinfo=UTC)
    # The receiving server puts its own results on top; any below came with the message, and
    # only a name I trust counts, as a server that adds none leaves the sender's on top.
    # Raw, not decoded: a sender's address in it could hold an encoded word that decodes to
    # results of its own.
    results = [v for k, v in headers.raw_items() if k.lower() == "authentication-results"]
    subject = _shown(header("Subject")) or "(no subject)"
    if len(subject) > MAX_SUBJECT:
        subject = subject[: MAX_SUBJECT - 1] + "…"
    auto = header("Auto-Submitted").strip().lower()
    return Message(
        uid=int(uid.group(1)),
        message_id=own,
        thread=thread or f"uid:{uid.group(1).decode()}",
        subject=subject,
        sender_name=_name(name) or email or "someone",
        sender_email=email,
        to_me=bool(to & me),
        bulk=bool(header("List-Id"))
        or header("Precedence").strip().lower() in ("bulk", "list", "junk")
        or (bool(auto) and auto != "no"),
        flagged="\\Flagged" in flag_set,
        seen="\\Seen" in flag_set,
        # One `@` only, so the domain checked is the one core reads as theirs.
        verified=bool(results)
        and email is not None
        and dmarc_passed(results[0], trusted, email.partition("@")[2]),
        date=date,
    )
