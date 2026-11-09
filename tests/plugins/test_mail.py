"""The mail plugin: reading a mailbox's headers over IMAP, and what it publishes and shows."""

import imaplib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from move_forward.asking import Asker, InitSetup
from move_forward.db import Database
from move_forward.graph import Entity, Graph, Ref, attrs
from move_forward.kinds import PERSON, Person
from move_forward.plugin import (
    ChangedRequest,
    CollectRequest,
    IdentityRequest,
    Response,
    SourceError,
)
from move_forward.plugins.mail import client
from move_forward.plugins.mail.config import GMAIL_MESSAGE_URL, Mailbox, MailConfig
from move_forward.plugins.mail.kinds import THREAD
from move_forward.plugins.mail.models import MAX_SUBJECT, MailSnapshot, Message
from move_forward.plugins.mail.panels import message_url, says
from move_forward.plugins.mail.panels import panels as mail_panels
from move_forward.plugins.mail.plugin import MailPlugin, merged
from move_forward.plugins.mail.repository import MIGRATIONS, MessageRepository
from move_forward.plugins.mail.setup import questions
from move_forward.repositories import ContactRepository, TagRepository
from move_forward.testing import Harness

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
ME = "me@gmail.com"
SECTION = {"host": "imap.gmail.com", "username": ME, "message_url": GMAIL_MESSAGE_URL}
SHARED = {"host": "imap.gmail.com", "message_url": GMAIL_MESSAGE_URL}
"""[mail] with accounts: no username of its own."""
HOME = {"username": ME}
WORK = {
    "host": "imap.work.example",
    "username": "me@work.example",
    "message_url": "mid:{message_id}",
}


def headers(
    subject: str = "Renewal",
    sender: str = "Alice <alice@acme.com>",
    to: str = ME,
    message_id: str = "m1@acme.com",
    references: str = "",
    extra: str = "",
) -> bytes:
    lines = [
        f"From: {sender}",
        f"To: {to}",
        f"Subject: {subject}",
        "Date: Sat, 07 Nov 2026 10:00:00 -0800",
        f"Message-ID: <{message_id}>",
        *([f"References: {references}"] if references else []),
        *([extra] if extra else []),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def test_a_message_is_read_from_its_headers() -> None:
    found = client.message(
        b"1 (UID 7 FLAGS (\\Flagged) BODY[HEADER.FIELDS (FROM)] {10}",
        headers(
            subject="=?utf-8?q?Caf=C3=A9_renewal?=",
            references="<root@acme.com> <m0@acme.com>",
        ),
        {ME},
    )
    assert found == Message(
        uid=7,
        message_id="m1@acme.com",
        thread="root@acme.com",
        subject="Café renewal",
        sender_name="Alice",
        sender_email="alice@acme.com",
        to_me=True,
        bulk=False,
        flagged=True,
        seen=False,
        verified=False,
        date=datetime(2026, 11, 7, 18, tzinfo=UTC),
    )


def test_what_a_sender_writes_badly_is_read_as_little_as_it_can_be() -> None:
    meta = b"1 (UID 7 FLAGS ())"
    found = client.message(meta, headers(sender=", :", subject="x" * 400), {ME})
    assert found is not None and (found.sender_name, found.sender_email) == ("someone", None)
    assert len(found.subject) == MAX_SUBJECT and found.subject.endswith("…")
    assert client.message(b"1 (FLAGS ())", headers(), {ME}) is None  # no UID to keep it by


def test_its_date_is_the_servers_not_what_the_sender_says() -> None:
    received = b'1 (UID 7 INTERNALDATE "07-Nov-2026 10:30:00 -0800" FLAGS ())'
    forged = headers(extra="Date: Fri, 31 Dec 9999 23:59:59 -1200").replace(
        b"Date: Sat, 07 Nov 2026 10:00:00 -0800\r\n", b""
    )
    found = client.message(received, forged, {ME})
    assert found is not None and found.date == datetime(2026, 11, 7, 18, 30, tzinfo=UTC)
    far = b'1 (UID 7 INTERNALDATE "31-Dec-9999 23:59:59 -1200" FLAGS ())'
    assert client.message(far, forged, {ME}) is not None  # nor a server date out of range
    # Without the server's word, an unreadable Date doesn't stop the refresh.
    found = client.message(b"1 (UID 7 FLAGS ())", forged, {ME})
    assert found is not None and found.date == datetime(1970, 1, 1, tzinfo=UTC)


TRUSTED = frozenset({"mx.example.com"})


@pytest.mark.parametrize(
    ("results", "verified"),
    [
        (["mx.example.com; dkim=pass; spf=pass; dmarc=pass header.from=acme.com"], True),
        (["MX.example.com 1; dmarc=pass (p=reject) header.from=Acme.com"], True),
        # Folded, as real ones are.
        (
            [
                "mx.example.com;\r\n\tspf=pass smtp.mailfrom=acme.com;\r\n\tdmarc=pass header.from=acme.com"
            ],
            True,
        ),
        # A second dmarc result means someone else's text got in: neither counts.
        (
            ["mx.example.com; dmarc=fail header.from=acme.com; dmarc=pass header.from=acme.com"],
            False,
        ),
        (["mx.example.com; dmarc=fail header.from=acme.com"], False),
        # The sender's own words: an envelope address, a comment, a quoted string.
        (
            [
                (
                    "mx.example.com; spf=pass (domain of dmarc=pass@evil.example) "
                    "smtp.mailfrom=dmarc=pass@evil.example; dmarc=fail header.from=acme.com"
                )
            ],
            False,
        ),
        (['mx.example.com; spf=pass smtp.mailfrom="x;dmarc=pass header.from=acme.com"'], False),
        # An encoded word in the sender's address, which would decode to results of its own.
        (
            [
                (
                    "mx.example.com; spf=pass (domain of "
                    "=?utf-8?q?=29=3Bdmarc=3Dpass_header.from=3Dacme.com=3B=28?=@evil.example) "
                    "smtp.mailfrom=x@evil.example; dmarc=fail header.from=acme.com"
                )
            ],
            False,
        ),
        # A pass, but for another domain than From's.
        (["mx.example.com; dmarc=pass header.from=evil.example"], False),
        # Not my server's: a server that adds none leaves the sender's on top.
        (["evil.example; dmarc=pass header.from=acme.com"], False),
        # One that came with the message, under the server's own, doesn't count.
        (["mx.example.com; dmarc=fail", "mx.example.com; dmarc=pass header.from=acme.com"], False),
        ([], False),
    ],
)
def test_a_sender_is_verified_only_by_my_servers_dmarc_for_their_domain(
    results: list[str], verified: bool
) -> None:
    extra = "\r\n".join(f"Authentication-Results: {r}" for r in results)
    found = client.message(b"1 (UID 7 FLAGS ())", headers(extra=extra), {ME}, TRUSTED)
    assert found is not None and found.verified is verified


def test_an_address_with_two_ats_is_never_verified() -> None:
    passed = "Authentication-Results: mx.example.com; dmarc=pass header.from=evil.example"
    raw = headers(sender='<"ceo@acme.com@x"@evil.example>', extra=passed)
    found = client.message(b"1 (UID 7 FLAGS ())", raw, {ME}, TRUSTED)
    assert found is not None and not found.verified


def test_gmails_server_is_known_and_others_are_set() -> None:
    assert _mailbox().trusted_auth_servers() == {"mx.google.com"}
    assert _mailbox(host="imap.example.com").trusted_auth_servers() == set()
    assert _mailbox(
        host="imap.example.com", auth_servers=["MX.Example.com"]
    ).trusted_auth_servers() == {"mx.example.com"}


def test_control_and_bidi_characters_are_dropped_from_what_is_shown() -> None:
    raw = headers(
        subject="=?utf-8?q?Invoice_=E2=80=AEfdp.exe=1B?=",
        sender="=?utf-8?q?Bob=E2=80=AE?= <b@x.com>",
    )
    found = client.message(b"1 (UID 7 FLAGS ())", raw, {ME})
    assert found is not None and (found.subject, found.sender_name) == ("Invoice fdp.exe", "Bob")


@pytest.mark.parametrize(
    "sender",
    [
        '"moc.emca\u202e"@evil.com',  # a bidi override in the address
        '"a b"@evil.com',
        "bo\u00adss@acme.com",  # a soft hyphen: reads as boss@acme.com
        "boss\u2060@acme.com",  # a word joiner
        "bo\u034fss@acme.com",  # a combining grapheme joiner
        "boss\ufe0f@acme.com",  # a variation selector
        "bo\u3164ss@acme.com",  # a Hangul filler
    ],
)
def test_an_address_that_cant_be_shown_as_one_isnt_kept(sender: str) -> None:
    found = client.message(b"1 (UID 7 FLAGS ())", headers(sender=f"<{sender}>"), {ME})
    assert found is not None and found.sender_email is None and not found.verified


def test_an_address_with_a_slash_is_shown_but_is_no_one() -> None:
    harness = mailbox(message(1, sender='"a/b"@evil.com'))
    [thread] = harness.entities()
    assert thread.attrs["sender_email"] == '"a/b"@evil.com' and thread.refs == []


def test_a_name_that_could_pass_for_an_address_shows_the_address() -> None:
    raw = headers(sender="=?utf-8?q?Boss_=3Cboss=40acme.com=3E=2C?= <x@evil.com>")
    found = client.message(b"1 (UID 7 FLAGS ())", raw, {ME})
    assert found is not None and (found.sender_name, found.sender_email) == ("x@evil.com",) * 2


def test_what_emoji_and_scripts_need_is_kept() -> None:
    raw = headers(subject="=?utf-8?q?Pair_=F0=9F=91=A9=E2=80=8D=F0=9F=92=BB?=")
    found = client.message(b"1 (UID 7 FLAGS ())", raw, {ME})
    assert found is not None and found.subject == "Pair 👩\u200d💻"


@pytest.mark.parametrize(
    "extra", ["List-Id: <news.acme.com>", "Precedence: bulk", "Auto-Submitted: auto-generated"]
)
def test_lists_and_automated_mail_are_bulk(extra: str) -> None:
    found = client.message(b"1 (UID 7 FLAGS ())", headers(extra=extra), {ME})
    assert found is not None and found.bulk


def test_mail_i_am_copied_on_is_not_to_me_and_flags_may_follow_the_headers() -> None:
    [(meta, raw)] = client._fetched(  # pyright: ignore[reportPrivateUsage]
        [
            (b"1 (UID 7 BODY[HEADER.FIELDS (FROM)] {10}", headers(to="bob@acme.com")),
            b" FLAGS (\\Seen))",
        ]
    )
    found = client.message(meta, raw, {ME})
    assert found is not None and (found.to_me, found.seen) == (False, True)


def test_a_reply_from_someone_else_doesnt_wear_a_conversations_flag() -> None:
    harness = mailbox(
        message(2, sender="stranger@evil.example", subject="Re: urgent wire"),
        message(1, sender="cust@acme.com", flagged=True),
    )
    [item] = _items(harness)
    assert "flagged" not in [getattr(b, "text", b) for b in item.badges]
    assert item.signals is not None and item.signals.importance == 0
    forged = mailbox(
        message(2, sender="cust@acme.com", verified=False, subject="Re: urgent wire"),
        message(1, sender="cust@acme.com", flagged=True),  # the real one, verified
    )
    [item] = _items(forged)
    assert "flagged" not in [getattr(b, "text", b) for b in item.badges]
    no_address = mailbox(
        message(2, sender="", sender_email=None, subject="Re"),
        message(1, sender="", sender_email=None, flagged=True),
    )
    [item] = _items(no_address)  # two without an address aren't one writer
    assert "flagged" not in [getattr(b, "text", b) for b in item.badges]
    flagged_by_writer = mailbox(
        message(2, sender="cust@acme.com"), message(1, flagged=True, sender="cust@acme.com")
    )
    [item] = _items(flagged_by_writer)
    assert "flagged" in [getattr(b, "text", b) for b in item.badges]


def test_a_thread_without_a_message_id_is_its_uid() -> None:
    raw = b"From: a@b.com\r\nSubject: hi\r\n\r\n"
    found = client.message(b"1 (UID 9 FLAGS ())", raw, {ME})
    assert found is not None and (found.message_id, found.thread) == (None, "uid:9")


def test_imap_dates_are_english_whatever_the_locale() -> None:
    assert client.imap_date(datetime(2026, 3, 4, tzinfo=UTC)) == "4-Mar-2026"


class FakeImap:
    """An IMAP server holding `messages` (UID, flags, headers), recording what it's asked."""

    def __init__(self, messages: list[tuple[int, str, bytes]], refuse: bool = False):
        self.messages = messages
        self.refuse = refuse
        self.capabilities: tuple[str, ...] = ("IMAP4REV1",)
        self.sent: dict[int, str] = {}
        """My sent mail by UID: its Message-IDs."""
        self.sent_to: dict[int, str] = {}
        """Whom each of my sent messages went to."""
        self.open = ""
        self.listed: list[bytes] | None = [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent Items"',
        ]
        self.closed: set[str] = set()
        """Mailboxes that won't open."""
        self.sent_fails = False
        self.aside: set[int] | None = set()
        """What Gmail files under Promotions or Social; None, a search that fails."""
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, host: str, port: int, ssl_context: object, timeout: int) -> "FakeImap":
        self.calls.append(("connect", host, port, timeout))
        return self

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", user, password))
        password.encode("ascii")  # as imaplib sends it
        if self.refuse:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b""]

    def select(self, mailbox: str, readonly: bool) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        if mailbox in self.closed:
            return "NO", [b"no such mailbox"]
        self.open = mailbox
        return "OK", [b"3"]

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.calls.append(("uid", command, *args))
        if self.open == '"Sent Items"':
            if command == "FETCH" and self.sent_fails:
                raise TimeoutError("timed out")
            if command == "SEARCH":
                return "OK", [b" ".join(str(u).encode() for u in self.sent)]
            wanted = {int(u) for u in args[0].split(",")}
            return "OK", [
                (
                    f"1 (UID {u} BODY[] {{1}}".encode(),
                    f"Message-ID: <{mid}>\r\nTo: {self.sent_to.get(u, '')}\r\n\r\n".encode(),
                )
                for u, mid in self.sent.items()
                if u in wanted
            ]
        if command == "SEARCH" and "X-GM-RAW" in args:
            if self.aside is None:
                raise imaplib.IMAP4.error("X-GM-RAW refused")
            return "OK", [b" ".join(str(u).encode() for u in sorted(self.aside))]
        if command == "SEARCH":
            wanted_flag, seen = ("\\Flagged", True) if "FLAGGED" in args else ("\\Seen", False)
            return "OK", [
                b" ".join(
                    str(u).encode()
                    for u, flags, _ in self.messages
                    if (wanted_flag in flags) == seen
                )
            ]
        wanted = {int(u) for u in args[0].split(",")}
        found: list[object] = []
        for uid, flags, raw in self.messages:
            if uid in wanted:
                found += [(f"1 (UID {uid} FLAGS ({flags}) BODY[] {{1}}".encode(), raw), b")"]
        return "OK", found

    def status(self, mailbox: str, items: str) -> tuple[str, list[bytes]]:
        self.calls.append(("status", mailbox, items))
        return "OK", [f'"INBOX" (MESSAGES {len(self.messages)} UNSEEN 1)'.encode()]

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout",))
        return "BYE", [b""]

    # Last, as its name would shadow `list` in the annotations after it.
    def list(self) -> tuple[str, list[bytes]]:
        self.calls.append(("list",))
        if self.listed is None:
            raise imaplib.IMAP4.error("LIST failed")
        return "OK", self.listed


@pytest.fixture
def imap(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeImap]:
    monkeypatch.setenv("MAIL_PASSWORD", "app-password")

    def serving(messages: list[tuple[int, str, bytes]], refuse: bool = False) -> FakeImap:
        server = FakeImap(messages, refuse)
        monkeypatch.setattr(client.imaplib, "IMAP4_SSL", server)
        return server

    return serving


def _config(**overrides: object) -> MailConfig:
    return MailConfig.model_validate({**SECTION, **overrides})


def _mailbox(**overrides: object) -> Mailbox:
    [mailbox] = _config(**overrides).mailboxes()
    return mailbox


def test_collect_reads_headers_only_without_marking_anything_read(
    imap: Callable[..., FakeImap],
) -> None:
    server = imap(
        [
            (4, "\\Seen \\Flagged", headers(subject="Marked", message_id="marked@acme.com")),
            (5, "", headers(message_id="old@acme.com")),
            (6, "", headers(subject="Newer", message_id="new@acme.com")),
        ]
    )
    snapshot = client.collect(_mailbox(since_days=7, max_messages=1), "me@work.com", NOW)
    # The newest unread, at most one, and every flagged one, however old.
    assert sorted(m.subject for m in snapshot.messages) == ["Marked", "Newer"]
    assert ("connect", "imap.gmail.com", 993, client.TIMEOUT_SECONDS) in server.calls
    assert ("login", ME, "app-password") in server.calls
    assert ("select", '"INBOX"', True) in server.calls  # read-only: EXAMINE
    assert ("uid", "SEARCH", "SINCE", "31-Oct-2026", "UNSEEN") in server.calls
    assert ("uid", "SEARCH", "SINCE", "31-Oct-2026", "FLAGGED") in server.calls
    [fetch] = [c for c in server.calls if c[:2] == ("uid", "FETCH")]
    assert fetch[2] == "4,6" and "BODY.PEEK[HEADER.FIELDS (" in str(fetch[3])
    assert server.calls[-1] == ("logout",)


def test_the_newest_are_kept_however_the_server_orders_its_search(
    imap: Callable[..., FakeImap],
) -> None:
    imap(  # IMAP doesn't promise SEARCH answers in UID order
        [
            (6, "", headers(subject="Newer", message_id="new@acme.com")),
            (5, "", headers(subject="Older", message_id="old@acme.com")),
        ]
    )
    snapshot = client.collect(_mailbox(max_messages=1), ME, NOW)
    assert [m.subject for m in snapshot.messages] == ["Newer"]


def test_a_refused_sign_in_says_what_to_do(imap: Callable[..., FakeImap]) -> None:
    imap([], refuse=True)
    with pytest.raises(SourceError, match="app password"):
        client.collect(_mailbox(), "me@work.com", NOW)


def test_a_password_imap_cant_send_says_so(
    imap: Callable[..., FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    imap([])
    monkeypatch.setenv("MAIL_PASSWORD", "pässword")
    with pytest.raises(SourceError, match="ASCII"):
        client.collect(_mailbox(), ME, NOW)


def test_no_password_says_how_to_set_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIL_PASSWORD", raising=False)

    def nothing_stored(service: str, account: str) -> None:
        return None

    monkeypatch.setattr(client.keychain, "read", nothing_stored)
    with pytest.raises(SourceError, match="mfd token set mail"):
        client.collect(_mailbox(), "me@work.com", NOW)


@pytest.mark.parametrize(
    ("overrides", "says"),
    [
        ({"host": "https://imap.gmail.com"}, "isn't a host name"),
        ({"message_url": "javascript:alert(1)"}, "must be an https:// link"),
        ({"message_url": "https://x/{subject}"}, "only {message_id}"),
    ],
)
def test_the_config_refuses_what_cant_work(overrides: dict[str, object], says: str) -> None:
    with pytest.raises(ValidationError, match=says):
        _config(**overrides)


def message(
    uid: int,
    subject: str = "Renewal",
    sender: str = "alice@acme.com",
    thread: str = "root@acme.com",
    **fields: Any,
) -> Message:
    values: dict[str, Any] = {
        "uid": uid,
        "message_id": f"m{uid}@acme.com",
        "thread": thread,
        "subject": subject,
        "sender_name": sender.split("@")[0].title(),
        "sender_email": sender,
        "to_me": True,
        "bulk": False,
        "flagged": False,
        "seen": False,
        "verified": True,
        "date": datetime(2026, 11, 7, uid, tzinfo=UTC),
        **fields,
    }
    return Message.model_validate(values)


def mailbox(*messages: Message, **section: object) -> Harness:
    harness = Harness(MailPlugin(), {**SECTION, **section}, now=NOW)
    harness.store(MailSnapshot(messages=list(messages)))
    return harness


def test_it_publishes_a_thread_per_conversation_pointing_at_whoever_wrote_last() -> None:
    harness = mailbox(
        message(3, subject="Re: APP-12 renewal", sender="bob@acme.com"),
        message(2),
        message(1, subject="Lunch", sender="carol@x.com", thread="other@x.com"),
    )
    entities = {e.ref: e for e in harness.entities()}
    thread = entities[Ref(kind=THREAD, id="root@acme.com")]
    assert thread.refs == [Ref(kind=PERSON, id="mail/bob@acme.com")]
    assert thread.mentions == ["Re: APP-12 renewal"]
    assert thread.attrs["unread"] == 2
    assert thread.url == (
        "https://mail.google.com/mail/u/me@gmail.com/#search/rfc822msgid:m3@acme.com"
    )
    bob = entities[Ref(kind=PERSON, id="mail/bob@acme.com")]
    assert (bob.attrs["email"], bob.title) == ("bob@acme.com", "Bob")
    assert Ref(kind=PERSON, id="mail/alice@acme.com") not in entities  # wrote earlier


def test_a_sender_the_server_didnt_vouch_for_is_no_one() -> None:
    harness = mailbox(message(1, sender="ceo@acme.com", verified=False))
    ContactRepository(harness.db).set("@acme.com", "customer", NOW)
    [thread] = harness.entities()
    assert thread.refs == []
    [item] = _items(harness)
    assert "customer" not in item.badges


def test_a_ticket_on_ice_its_subject_names_doesnt_hide_mail() -> None:
    """Its sender wrote the subject, not me."""
    harness = mailbox(message(1, subject="About APP-1"))
    [thread] = [e for e in harness.entities() if e.ref.kind == THREAD]
    assert thread.mentions == ["About APP-1"] and not thread.mentions_hide


def _items(harness: Harness) -> list[Any]:
    return [i for p in harness.dashboard().panels if p.key == "mail.inbox" for i in p.items]


def test_the_panel_lists_people_first_and_mail_from_lists_last() -> None:
    harness = mailbox(
        message(3, subject="Newsletter", sender="news@acme.com", thread="n", bulk=True),
        message(2, subject="FYI", sender="bob@acme.com", thread="c", to_me=False),
        message(1, subject="Renewal", flagged=True),
    )
    items = _items(harness)
    assert [i.title for i in items] == ["Renewal", "FYI", "Newsletter"]
    renewal, fyi, newsletter = items
    assert "flagged" in [getattr(b, "text", b) for b in renewal.badges]
    assert "cc" in fyi.badges
    assert newsletter.deferred
    assert renewal.details[0] == "from Alice <alice@acme.com>"


def test_act_now_has_mail_sent_to_me_with_its_sender_waiting_but_not_lists() -> None:
    harness = mailbox(
        message(2, subject="Newsletter", sender="news@acme.com", thread="n", bulk=True),
        message(1, subject="Renewal"),
    )
    [item] = harness.act_now()
    assert item.title.endswith("Renewal")
    assert any("Alice <alice@acme.com> waiting" in d for d in item.details)  # who, not a name


def test_a_customers_mail_is_badged_and_ranked_higher() -> None:
    harness = mailbox(
        message(2, subject="Lunch", sender="carol@x.com", thread="other"),
        message(1, subject="Renewal"),
    )
    ContactRepository(harness.db).set("@acme.com", "customer", NOW)
    items = _items(harness)
    assert [i.title for i in items] == ["Renewal", "Lunch"]
    assert "customer" in items[0].badges
    assert harness.act_now()[0].title.endswith("Renewal")


def test_what_a_crm_says_about_a_sender_counts_for_their_mail() -> None:
    crm = Entity(
        ref=Ref(kind=PERSON, id="crm.example.com/42"),
        title="Alice",
        attrs=attrs(Person(handle="42", email="Alice@acme.com", contact_type="customer")),
    )
    harness = mailbox(message(2, subject="Lunch", sender="carol@x.com", thread="t"), message(1))
    harness.with_entities([crm])
    assert "customer" in _items(harness)[0].badges


def test_an_app_link_goes_through_the_open_page_which_sends_the_browser_on() -> None:
    harness = mailbox(message(1), message_url="message:%3C{message_id}%3E")
    [item] = _items(harness)
    assert item.url == "/mail/open?thread=root%40acme.com"
    [thread, _] = harness.entities()
    assert thread.url == item.url
    found = harness.page("open", {"thread": ["root@acme.com"]})
    assert isinstance(found, Response)
    assert (found.status, found.headers["Location"]) == (303, "message:%3Cm1@acme.com%3E")
    missing = harness.page("open", {"thread": ["gone@acme.com"]})
    assert isinstance(missing, Response) and missing.status == 404
    twice = harness.page("open", {"thread": ["root@acme.com", "other"]})  # the first counts
    assert isinstance(twice, Response) and twice.headers["Location"] == "message:%3Cm1@acme.com%3E"


def test_a_message_id_cant_break_out_of_its_place_in_the_link() -> None:
    [item] = _items(mailbox(message(1, message_id="a/b#c?d&e=f@x")))
    assert item.url == (
        "https://mail.google.com/mail/u/me@gmail.com/#search/rfc822msgid:a%2Fb%23c%3Fd%26e%3Df@x"
    )


def test_without_a_message_url_mail_is_not_linked() -> None:
    [item] = _items(mailbox(message(1), message_url=""))
    assert item.url is None


def test_changed_says_so_when_the_mailbox_counts_move(imap: Callable[..., FakeImap]) -> None:
    server = imap([(1, "", headers())])
    harness = mailbox()
    plugin = MailPlugin()

    def changed() -> bool:
        return plugin.changed(ChangedRequest(harness.ctx, harness.graph())).changed

    assert not changed()  # the first look has nothing to compare with
    assert not changed()
    server.messages.append((2, "", headers(message_id="m2@acme.com")))
    assert changed()


def test_init_guesses_gmail_from_my_email() -> None:
    def ask(question: str, default: str, check: Callable[[str], str] | None = None) -> str:
        return check(default) if check else default

    found = questions(cast(Asker, ask), InitSetup(email=ME, cwd=Path()))
    assert found == {"host": "imap.gmail.com", "username": ME, "message_url": GMAIL_MESSAGE_URL}


def test_a_failed_store_leaves_what_was_kept() -> None:
    harness = mailbox(message(1))
    clash = [message(2), message(2, subject="same uid")]  # the second can't go in
    with pytest.raises(sqlite3.IntegrityError):
        MessageRepository(harness.db).replace(MailSnapshot(messages=clash))
    assert [m.uid for m in MessageRepository(harness.db).load().messages] == [1]


def test_many_messages_are_fetched_in_chunks(
    imap: Callable[..., FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "FETCH_CHUNK", 2)
    server = imap([(uid, "\\Flagged", headers(message_id=f"m{uid}@x")) for uid in range(1, 6)])
    snapshot = client.collect(_mailbox(), ME, NOW)
    fetches = [c[2] for c in server.calls if c[:2] == ("uid", "FETCH")]
    assert fetches == ["1,2", "3,4", "5"] and len(snapshot.messages) == 5


def test_gmails_important_label_is_read() -> None:
    meta = b'1 (UID 7 X-GM-LABELS ("\\\\Important" "\\\\Inbox") FLAGS ())'
    found = client.message(meta, headers(), {ME})
    assert found is not None and found.important
    plain = client.message(b'1 (UID 7 X-GM-LABELS ("\\\\Inbox") FLAGS ())', headers(), {ME})
    assert plain is not None and not plain.important
    # A label with parentheses before it doesn't hide it.
    meta = b'1 (UID 7 X-GM-LABELS ("Receipts (2024)" "\\\\Important") FLAGS ())'
    found = client.message(meta, headers(), {ME})
    assert found is not None and found.important
    # A label merely containing it isn't it; a bare one is.
    meta = b'1 (UID 7 X-GM-LABELS ("Not \\\\Important") FLAGS ())'
    found = client.message(meta, headers(), {ME})
    assert found is not None and not found.important
    found = client.message(b"1 (UID 7 X-GM-LABELS (\\Important) FLAGS ())", headers(), {ME})
    assert found is not None and found.important


def test_gmail_labels_are_read_its_own_and_mine() -> None:
    meta = (
        b'1 (UID 7 X-GM-LABELS (\\Inbox "\\\\Starred" Garbage "Work/Garbage Day"'
        b' "say \\"hi\\"" &AOk-t&AOk- R&-D &Zm- &2D3-) FLAGS ())'
    )
    found = client.message(meta, headers(), {ME})
    assert found is not None
    assert found.labels == [
        "\\Inbox",
        "\\Starred",
        "Garbage",
        "Work/Garbage Day",
        'say "hi"',
        "\u00e9t\u00e9",
        "R&D",
        "&Zm-",  # not modified UTF-7: kept as it came
        "&2D3-",
    ]


def test_a_threads_labels_are_its_tags_and_kept_between_refreshes() -> None:
    harness = mailbox(
        message(2, labels=["Garbage", "Bills", "\\Inbox"]),
        message(1, labels=["Garbage"], sender="x@spam.com", verified=False),
    )
    [thread] = [e for e in harness.entities() if e.ref.kind == THREAD]
    assert thread.tags == ["Garbage", "Bills", "\\Inbox"]  # whoever sent the mail
    kept = MessageRepository(harness.db).load().messages
    assert [m.labels for m in kept] == [["Garbage", "Bills", "\\Inbox"], ["Garbage"]]
    assert MessageRepository(harness.db).latest("root@acme.com") == kept[0]


def test_labels_are_asked_for_only_from_gmail(imap: Callable[..., FakeImap]) -> None:
    server = imap([(1, "", headers())])
    client.collect(_mailbox(), ME, NOW)
    assert "X-GM-LABELS" not in str([c for c in server.calls if c[:2] == ("uid", "FETCH")])
    server = imap([(1, "", headers())])
    server.capabilities = ("IMAP4REV1", client.GMAIL)
    client.collect(_mailbox(), ME, NOW)
    assert "X-GM-LABELS" in str([c for c in server.calls if c[:2] == ("uid", "FETCH")])


def test_mail_gmail_finds_important_ranks_higher_but_not_a_lists() -> None:
    harness = mailbox(
        message(3, subject="Plain", sender="carol@x.com", thread="p"),
        message(2, subject="Digest", sender="news@x.com", thread="d", bulk=True, important=True),
        message(1, subject="Contract", important=True),
    )
    items = {i.title: i for i in _items(harness)}
    assert items["Contract"].signals is not None and items["Contract"].signals.importance == 1
    assert "important" in items["Contract"].badges
    assert items["Digest"].signals is not None and items["Digest"].signals.importance == 0
    assert "important" not in items["Digest"].badges
    assert next(i.title for i in _items(harness)) == "Contract"


def test_mail_answering_mine_replies_to_me(imap: Callable[..., FakeImap]) -> None:
    server = imap(
        [
            (1, "", headers(message_id="r1@acme.com", references="<mine@me>")),
            (2, "", headers(message_id="n1@acme.com")),
        ]
    )
    server.sent = {10: "mine@me"}
    snapshot = client.collect(_mailbox(), ME, NOW)
    assert {m.message_id: m.replies_to_me for m in snapshot.messages} == {
        "r1@acme.com": True,
        "n1@acme.com": False,
    }
    assert ("select", '"Sent Items"', True) in server.calls  # read-only, found by its \\Sent


def test_my_newest_sent_mail_is_read_however_the_server_orders_its_search(
    imap: Callable[..., FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "SENT_CAP", 1)
    server = imap([(1, "", headers(message_id="r1@acme.com", references="<mine@me>"))])
    server.sent = {11: "mine@me", 10: "old@me"}  # answered out of UID order
    [found] = client.collect(_mailbox(), ME, NOW).messages
    assert found.replies_to_me


def test_a_reply_to_me_is_important() -> None:
    harness = mailbox(message(2, subject="New", thread="n"), message(1, replies_to_me=True))
    items = {i.title: i for i in _items(harness)}
    assert "replies to me" in items["Renewal"].badges
    assert items["Renewal"].signals is not None and items["Renewal"].signals.importance == 1


def test_reading_my_sent_mail_finds_it_by_its_flag_or_config_and_never_fails_the_inbox(
    imap: Callable[..., FakeImap],
) -> None:
    reply = headers(message_id="r@x", extra="In-Reply-To: <mine@me>")
    server = imap([(1, "", reply)])
    server.sent = {10: "mine@me"}
    server.listed = [b'(\\HasNoChildren) "/" "INBOX"']  # nothing marked \\Sent
    assert not client.collect(_mailbox(), ME, NOW).messages[0].replies_to_me
    server = imap([(1, "", reply)])
    server.sent = {10: "mine@me"}
    server.listed = []
    configured = client.collect(_mailbox(sent_mailbox="Sent Items"), ME, NOW)
    assert configured.messages[0].replies_to_me  # sent_mailbox needs no LIST
    for broken in ("closed", "list", "fetch"):
        server = imap([(1, "", reply)])
        server.sent = {10: "mine@me"}
        if broken == "closed":
            server.closed = {'"Sent Items"'}
        elif broken == "list":
            server.listed = None
        else:
            server.sent_fails = True  # a timeout fetching my sent mail
        [found] = client.collect(_mailbox(), ME, NOW).messages  # the inbox still comes
        assert not found.replies_to_me


def test_only_a_messages_parent_says_its_answering_me() -> None:
    raw = headers(references="<mine@me> <someone@x>")  # mine is long ago in the chain
    assert client._referenced(raw) == {"someone@x"}  # pyright: ignore[reportPrivateUsage]
    raw = headers(references="<mine@me>", extra="In-Reply-To: <other@x>")
    assert client._referenced(raw) == {"other@x"}  # pyright: ignore[reportPrivateUsage]


def test_a_quoted_sent_mailbox_name_is_unescaped() -> None:
    class Listing:
        def list(self) -> tuple[str, list[bytes]]:
            return "OK", [b'(\\Sent) "/" "My \\"Sent\\" \\\\ box"']

    found = client.sent_mailbox(cast(Any, Listing()), _mailbox())
    assert found == 'My "Sent" \\ box'


def test_a_lists_reply_to_me_isnt_important() -> None:
    harness = mailbox(message(1, replies_to_me=True, bulk=True))
    [item] = _items(harness)
    assert "replies to me" not in item.badges
    assert item.signals is not None and item.signals.importance == 0


def test_mail_about_my_open_work_says_so_and_is_important() -> None:
    ticket = Entity(ref=Ref(kind="ticket", id="https://j/APP-12"), title="APP-12 Fix login")
    done = Entity(ref=Ref(kind="ticket", id="https://j/APP-9"), title="APP-9 Old", lifecycle="done")
    snapshot = MailSnapshot(
        messages=[
            message(3, subject="Lunch", sender="carol@x.com", thread="l"),
            message(2, subject="About APP-9", sender="dan@x.com", thread="o"),
            message(1, subject="Re: APP-12"),
        ]
    )
    threads = [
        Entity(ref=Ref(kind=THREAD, id="root@acme.com"), title="Re: APP-12", refs=[ticket.ref]),
        Entity(ref=Ref(kind=THREAD, id="o"), title="About APP-9", refs=[done.ref]),
    ]
    graph = Graph([ticket, done, *threads], linking={"ticket": None})
    [panel] = mail_panels(_config(), "mail", snapshot, graph)
    items = {i.title: i for i in panel.items}
    assert "about APP-12 Fix login" in items["Re: APP-12"].details
    assert items["Re: APP-12"].signals is not None and items["Re: APP-12"].signals.importance == 1
    assert not any(d.startswith("about") for d in items["About APP-9"].details)  # done
    assert items["Lunch"].signals is not None and items["Lunch"].signals.importance == 0


def test_only_a_verified_senders_subject_says_what_its_about() -> None:
    vouched = mailbox(message(1, subject="Re: APP-12"))
    [thread] = [e for e in vouched.entities() if e.ref.kind == THREAD]
    assert thread.mentions == ["Re: APP-12"]
    forged = mailbox(message(1, subject="Re: APP-12 urgent", verified=False))
    [thread] = [e for e in forged.entities() if e.ref.kind == THREAD]
    assert thread.mentions == []  # typing a key doesn't make it about my ticket


def test_someone_i_wrote_to_twice_is_known(imap: Callable[..., FakeImap]) -> None:
    server = imap(
        [
            (1, "", headers(sender="Alice <alice@acme.com>")),
            (2, "", headers(sender="bob@x.com", message_id="b@x")),
            (3, "", headers(sender=ME, message_id="me@x")),
        ]
    )
    server.sent = {10: "s1@me", 11: "s2@me", 12: "s3@me"}
    server.sent_to = {
        10: f"Alice <ALICE@acme.com>\r\nCc: {ME}",  # copying myself
        11: f"carol@y.com, {ME}\r\nCc: alice@acme.com, bob@x.com",
        12: "carol@y.com",
    }
    found = {m.sender_email: m.known for m in client.collect(_mailbox(), ME, NOW).messages}
    # Alice twice (To and Cc), bob once, and me never, however often I'm copied.
    assert found == {"alice@acme.com": True, "bob@x.com": False, ME: False}


def test_a_known_sender_is_important_only_when_verified() -> None:
    harness = mailbox(message(2, thread="f", known=True, verified=False), message(1, known=True))
    items = {i.fingerprint: i for i in _items(harness)}
    assert "known" in items["m1@acme.com"].badges
    assert items["m1@acme.com"].signals is not None and items["m1@acme.com"].signals.importance == 1
    assert "known" not in items["m2@acme.com"].badges  # a forged From mustn't borrow it


def test_an_item_shows_one_of_replies_to_me_known_and_important() -> None:
    harness = mailbox(message(1, replies_to_me=True, known=True, important=True))
    [item] = _items(harness)
    shown = [b for b in item.badges if b in ("replies to me", "known", "important")]
    assert shown == ["replies to me"]


def test_gmails_promotions_and_social_read_like_lists(imap: Callable[..., FakeImap]) -> None:
    server = imap(
        [(1, "", headers(message_id="sale@shop")), (2, "", headers(message_id="hi@friend"))]
    )
    server.capabilities = ("IMAP4REV1", client.GMAIL)
    server.aside = {1}
    found = {m.message_id: m.bulk for m in client.collect(_mailbox(), ME, NOW).messages}
    assert found == {"sale@shop": True, "hi@friend": False}
    # A reply to me filed under Promotions is still a person writing to me, verified; one who
    # isn't verified could name my Message-ID to escape it, and is put aside.
    vouched = "Authentication-Results: mx.google.com; dmarc=pass header.from=acme.com"
    for extra, aside in ((vouched, False), ("", True)):
        raw = headers(message_id="r@contractor", extra=f"In-Reply-To: <mine@me>\r\n{extra}")
        misfiled = imap([(1, "", raw)])
        misfiled.capabilities = ("IMAP4REV1", client.GMAIL)
        misfiled.aside, misfiled.sent = {1}, {10: "mine@me"}
        [reply] = client.collect(_mailbox(), ME, NOW).messages
        assert (reply.replies_to_me, reply.bulk) == (True, aside)
    # Nor is someone I know, verified; one who isn't verified is put aside.
    for verified, aside in ((True, False), (False, True)):
        results = "Authentication-Results: mx.google.com; dmarc=pass header.from=friend.com"
        raw = headers(sender="pal@friend.com", message_id="f@x", extra=results if verified else "")
        known = imap([(1, "", raw)])
        known.capabilities = ("IMAP4REV1", client.GMAIL)
        known.aside, known.sent, known.sent_to = (
            {1},
            {10: "a@me", 11: "b@me"},
            {10: "pal@friend.com", 11: "pal@friend.com"},
        )
        [found] = client.collect(_mailbox(), ME, NOW).messages
        assert (found.known, found.bulk) == (True, aside)
    failing = imap([(1, "", headers(message_id="sale@shop"))])
    failing.capabilities, failing.aside = ("IMAP4REV1", client.GMAIL), None
    assert not client.collect(_mailbox(), ME, NOW).messages[0].bulk  # nothing put aside
    plain = imap([(1, "", headers(message_id="sale@shop"))])
    plain.aside = {1}  # not Gmail: not asked
    assert not client.collect(_mailbox(), ME, NOW).messages[0].bulk
    assert not any("X-GM-RAW" in c for c in plain.calls)


def test_urgent_words_lift_only_someone_i_know() -> None:
    harness = mailbox(
        message(3, subject="Outage in prod", thread="a", known=True),
        message(2, subject="Outage: act now", thread="b", sender="spam@x.com"),  # not known
        message(1, subject="outage", thread="c", known=True, verified=False),
        message(4, subject="Lunch?", thread="d", known=True),
        urgent_words=["outage"],
    )
    items = {i.fingerprint: i for i in _items(harness)}
    assert "urgent" in [getattr(b, "text", b) for b in items["m3@acme.com"].badges]
    importance = {f: i.signals.importance for f, i in items.items() if i.signals}
    assert (importance["m3@acme.com"], importance["m4@acme.com"]) == (2, 1)
    for other in ("m2@acme.com", "m1@acme.com", "m4@acme.com"):
        assert "urgent" not in [getattr(b, "text", b) for b in items[other].badges]


@pytest.mark.parametrize(
    ("words", "subject", "found"),
    [
        (["action required"], "Action  required: renew", True),
        (["action required"], "action\u00a0required", True),
        (["St\u00f6rung"], "Sto\u0308rung in prod", True),  # NFD
        (["[URGENT]"], "[urgent] disk full", True),
        (["C++"], "C++ review", True),
        (["digest"], "Digestible", False),
        ([" ", ""], "anything", False),
    ],
)
def test_subject_words_match_whole_words_however_spaced_or_encoded(
    words: list[str], subject: str, found: bool
) -> None:
    assert says(words, subject) is found


def test_low_words_rank_mail_with_lists_but_not_from_someone_i_know() -> None:
    harness = mailbox(
        message(3, subject="Weekly digest", thread="a", sender="news@list.com"),
        message(2, subject="Digest of the week", thread="b", flagged=True),
        message(1, subject="Digestible news", thread="c"),  # not the whole word
        message(4, subject="Re: digest rollout, sign off?", thread="d", known=True),
        message(5, subject="Re: your digest idea", thread="e", replies_to_me=True),
        low_words=["digest"],
    )
    deferred = {i.fingerprint: i.deferred for i in _items(harness)}
    assert deferred == {
        "m3@acme.com": True,
        "m2@acme.com": False,
        "m1@acme.com": False,
        "m4@acme.com": False,  # someone I know
        "m5@acme.com": False,  # replies to me
    }
    titles = [i.title for i in harness.act_now()]
    assert not any("Weekly digest" in t for t in titles)


def test_mail_naming_a_tag_on_open_work_is_about_it_but_a_senders_own_tag_isnt() -> None:
    harness = mailbox(
        message(6, subject="Garbage day moved", thread="g"),
        message(5, subject="garbage day!!", thread="u", sender="x@spam.com", verified=False),
        message(4, subject="#sale ends Friday", thread="s"),
        message(3, subject="Sale on now", thread="t"),
        message(2, subject="Your invoice is overdue", thread="i", sender="x@billing.example"),
        message(1, subject="Re: APP-9 lunch", thread="d"),
    )
    harness.with_entities(
        [
            Entity(ref=Ref(kind="notes.todo", id="7"), title="take the #garbage-day bins out"),
            Entity(ref=Ref(kind="notes.todo", id="8"), title="pay the #invoice"),
            Entity(ref=Ref(kind="notes.todo", id="9"), title="#lunch", lifecycle="done"),
            Entity(  # done, carrying a tag something open carries too
                ref=Ref(kind="ticket", id="https://j/APP-9"),
                title="APP-9 #garbage-day bins",
                lifecycle="done",
            ),
        ]
    )
    items = {i.fingerprint: i for i in _items(harness)}

    def about(fingerprint: str) -> list[str]:
        return [d for d in items[fingerprint].details if d.startswith("about")]

    def importance(fingerprint: str) -> int:
        signals = items[fingerprint].signals
        assert signals is not None
        return signals.importance

    assert (about("m6@acme.com"), importance("m6@acme.com")) == (["about #garbage-day"], 1)
    assert about("m5@acme.com") == []  # unverified
    assert about("m4@acme.com") == about("m3@acme.com") == []  # a sender's #tag is no tag
    # One word anyone may write: said, but it lifts nothing.
    assert (about("m2@acme.com"), importance("m2@acme.com")) == (["about #invoice"], 0)
    # Only what its own subject names: not the tags of the done ticket it names, nor #lunch,
    # carried by nothing open.
    assert about("m1@acme.com") == []


def test_mail_i_tag_is_about_the_tag_while_something_else_open_has_it() -> None:
    harness = mailbox(message(1, subject="Garbage day moved", sender="city@town.example"))
    thread = Ref(kind=THREAD, id="root@acme.com")
    TagRepository(harness.db).add(thread, "garbage-day", NOW)
    [alone] = _items(harness)
    assert "#garbage-day" in alone.badges
    assert not any(d.startswith("about") for d in alone.details)  # only it has the tag
    harness.with_entities(
        [Entity(ref=Ref(kind="notes.todo", id="7"), title="take the #garbage-day bins out")]
    )
    [shared] = _items(harness)  # tagging it by hand doesn't lose what its subject names
    assert "about #garbage-day" in shared.details
    assert shared.signals is not None and shared.signals.importance == 1
    harness.store(  # a forged reply naming the conversation takes on nothing I put on it
        MailSnapshot(
            messages=[
                message(2, subject="Pay this invoice now", sender="x@evil.com", verified=False),
                message(1, subject="Garbage day moved", sender="city@town.example"),
            ]
        )
    )
    [forged] = _items(harness)
    assert not any(d.startswith("about") for d in forged.details)
    assert forged.signals is not None and forged.signals.importance == 0


def test_each_sub_table_is_an_account_taking_from_mail_what_it_leaves_out() -> None:
    config = MailConfig.model_validate(
        {**SHARED, "since_days": 7, "home": HOME, "work": {**WORK, "mailbox": "Work"}}
    )
    home, work = config.mailboxes()
    assert (home.name, home.host, home.username, home.mailbox) == (
        "home",
        "imap.gmail.com",
        ME,
        "INBOX",
    )
    assert (work.name, work.host, work.username, work.mailbox) == (
        "work",
        "imap.work.example",
        "me@work.example",
        "Work",
    )
    assert work.since_days == home.since_days == 7  # what's for every mailbox
    # A password is never shared: each account's is its own.
    assert (home.token_env, home.keychain_service) == (
        "MAIL_PASSWORD_HOME",
        "move-forward-mail-home",
    )
    assert (work.token_env, work.keychain_service) == (
        "MAIL_PASSWORD_WORK",
        "move-forward-mail-work",
    )
    lone = _config().mailboxes()
    assert [(m.name, m.token_env, m.keychain_service) for m in lone] == [
        ("", "MAIL_PASSWORD", "move-forward-mail")
    ]


@pytest.mark.parametrize(
    ("section", "said"),
    [
        ({"username": ME}, r"\[mail\] needs a host"),
        ({"work": {"host": "imap.work.example"}}, r"\[mail.work\] needs a host and a username"),
        ({**SECTION, "work": WORK}, r"\[mail\] has accounts, so its username names no mailbox"),
        ({**SHARED, "Work": WORK}, "lower-case letters"),
        ({"auth_servers": ["mx.example.com"], "work": WORK}, "no account takes them"),
        ({**SHARED, "auth_servers": ["mx.google.com"], "work": WORK}, "no account takes them"),
        (
            {**SHARED, "auth_servers": ["x"], "home": {**HOME, "auth_servers": ["y"]}},
            "no account takes them",
        ),
        ({**SHARED, "work": {**WORK, "since_days": 3}}, "since_days"),  # [mail]'s alone
    ],
)
def test_a_mailbox_needs_a_host_and_a_username_and_an_account_a_name(
    section: dict[str, object], said: str
) -> None:
    with pytest.raises(ValidationError, match=said):
        MailConfig.model_validate(section)


def test_every_mailbox_is_read_and_a_message_in_two_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collect(mailbox: Mailbox, email: str, now: datetime) -> MailSnapshot:
        both = message(2, subject="To both", account=mailbox.name, flagged=mailbox.name == "work")
        own = message(
            3 if mailbox.name == "home" else 1, subject=f"For {mailbox.name}", account=mailbox.name
        ).model_copy(update={"message_id": f"{mailbox.name}@acme.com"})
        return MailSnapshot(messages=[own, both])

    monkeypatch.setattr(client, "collect", collect)
    harness = Harness(MailPlugin(), {**SHARED, "home": HOME, "work": WORK}, now=NOW)
    snapshot = cast(MailSnapshot, harness.plugin.collect(CollectRequest(harness.ctx)))
    assert [(m.subject, m.account, m.flagged) for m in snapshot.messages] == [
        ("For home", "home", False),
        ("To both", "home", True),  # home's copy, flagged in work
        ("For work", "work", False),
    ]


def test_a_message_without_a_message_id_is_its_mailboxs_own_conversation(
    imap: Callable[..., FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAIL_PASSWORD_WORK", "app-password")
    imap([(4, "", headers(message_id=""))])
    work = MailConfig.model_validate({**SHARED, "work": HOME}).mailboxes()[0]
    [found] = client.collect(work, ME, NOW).messages
    assert (found.account, found.thread) == ("work", "uid:work:4")


def test_a_message_links_by_its_own_mailboxs_template() -> None:
    config = MailConfig.model_validate({**SHARED, "home": HOME, "work": WORK})
    home = message(1, account="home")
    work = message(2, account="work")
    assert message_url(config, home) == (
        "https://mail.google.com/mail/u/me@gmail.com/#search/rfc822msgid:m1@acme.com"
    )
    assert message_url(config, work) == "mid:m2@acme.com"
    assert message_url(config, message(3, account="gone")) is None  # since taken out


def test_two_mailboxes_keep_their_messages_apart_and_say_whose_they_are() -> None:
    harness = Harness(MailPlugin(), {**SHARED, "home": HOME, "work": WORK}, now=NOW)
    snapshot = MailSnapshot(
        messages=[
            message(1, account="home", labels=["\\Inbox"]),
            message(1, account="work", thread="other@acme.com"),  # the same UID
        ]
    )
    harness.store(snapshot)
    assert MessageRepository(harness.db).load() == snapshot
    [panel] = [p for p in harness.dashboard().panels if p.key == "mail.inbox"]
    assert "in home's INBOX and work's INBOX" in panel.description
    plugin = MailPlugin()
    config = cast(MailConfig, harness.ctx.config)
    assert sorted(plugin.tokens(config)) == ["home", "work"]
    identity = plugin.identity(IdentityRequest(harness.ctx, snapshot))
    assert identity is not None and identity.login == "me@gmail.com, me@work.example"


def test_an_unverified_message_in_two_mailboxes_is_one_conversation_counted_once() -> None:
    harness = Harness(MailPlugin(), {**SHARED, "home": HOME, "work": WORK}, now=NOW)
    copies = [message(1, account=a, verified=False) for a in ("home", "work")]
    harness.store(MailSnapshot(messages=[*copies, message(2, account="work", verified=False)]))
    [item] = _items(harness)
    assert "2 unread" in item.badges and "3 unread" not in item.badges


def test_a_mailbox_changing_refreshes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    counts = {"home": "1", "work": "1"}

    def status(mailbox: Mailbox, email: str) -> str:
        return counts[mailbox.name]

    monkeypatch.setattr(client, "status", status)
    harness = Harness(MailPlugin(), {**SHARED, "home": HOME, "work": WORK}, now=NOW)
    plugin = MailPlugin()
    assert not plugin.changed(ChangedRequest(harness.ctx, harness.graph())).changed
    counts["work"] = "2"
    assert plugin.changed(ChangedRequest(harness.ctx, harness.graph())).changed


def test_the_messages_kept_before_accounts_are_kept_as_mails_own() -> None:
    db = Database(":memory:")
    db.migrate("mail", MIGRATIONS[: [m.name for m in MIGRATIONS].index("0006_accounts")])
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO mail_messages VALUES (7, 0, 'm7@acme.com', 'root@acme.com', 'Hi', 'A',"
            " 'a@acme.com', 1, 0, 0, 0, 1, '2026-11-07T07:00:00+00:00', 0, 0, 0)"
        )
        conn.execute("INSERT INTO mail_message_labels VALUES (7, '\\Inbox')")
    db.migrate("mail", MIGRATIONS)
    [kept] = MessageRepository(db).load().messages
    assert (kept.account, kept.uid, kept.labels) == ("", 7, ["\\Inbox"])


def test_a_message_claiming_anothers_message_id_takes_nothing_from_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = message(1, subject="Renewal", flagged=True)
    forged = message(2, subject="Pay now", sender="evil@evil.com", verified=False)
    forged = forged.model_copy(update={"message_id": real.message_id})

    def collect(mailbox: Mailbox, email: str, now: datetime) -> MailSnapshot:
        return MailSnapshot(messages=[forged, real])

    monkeypatch.setattr(client, "collect", collect)
    harness = Harness(MailPlugin(), SECTION, now=NOW)  # one mailbox: nothing is merged in it
    snapshot = cast(MailSnapshot, harness.plugin.collect(CollectRequest(harness.ctx)))
    assert [(m.sender_email, m.flagged) for m in snapshot.messages] == [
        ("evil@evil.com", False),
        ("alice@acme.com", True),
    ]


def test_a_copy_in_another_mailbox_adds_whats_about_me_there_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collect(mailbox: Mailbox, email: str, now: datetime) -> MailSnapshot:
        if mailbox.name == "home":
            return MailSnapshot(messages=[message(1, account="home", to_me=False)])
        copy = message(1, account="work", to_me=True, replies_to_me=True)
        other = message(2, account="work", sender="mallory@acme.com", important=True)
        other = other.model_copy(update={"message_id": "m1@acme.com"})  # another sender
        return MailSnapshot(messages=[copy, copy.model_copy(update={"uid": 9}), other])

    monkeypatch.setattr(client, "collect", collect)
    harness = Harness(MailPlugin(), {**SHARED, "home": HOME, "work": WORK}, now=NOW)
    snapshot = cast(MailSnapshot, harness.plugin.collect(CollectRequest(harness.ctx)))
    found = [(m.account, m.uid, m.to_me, m.replies_to_me, m.important) for m in snapshot.messages]
    assert sorted(found) == [
        ("home", 1, True, True, False),  # what work's copy says of me
        ("work", 2, True, False, True),  # another sender's: its own
        ("work", 9, True, True, False),  # a second claim in work: its own
    ]


def test_an_account_on_its_own_server_trusts_only_its_own_results() -> None:
    config = MailConfig.model_validate(
        {**SHARED, "auth_servers": ["mx.google.com"], "home": HOME, "work": WORK}
    )
    home, work = config.mailboxes()
    assert home.trusted_auth_servers() == {"mx.google.com"}
    assert work.trusted_auth_servers() == set()  # imap.work.example's are unknown


@pytest.mark.parametrize(
    ("home", "work", "found"),
    [
        (True, True, [("home", True)]),
        (False, False, [("home", False), ("work", True)]),
        (True, False, [("home", False), ("work", True)]),
        (False, True, [("home", False), ("work", True)]),
    ],
)
def test_a_copy_merges_only_when_both_senders_are_verified(
    home: bool, work: bool, found: list[tuple[str, bool]]
) -> None:
    first = message(1, account="home", verified=home)
    copy = message(1, account="work", verified=work, flagged=True)
    assert [(m.account, m.flagged) for m in merged([first], [copy])] == found


def test_an_account_on_mails_own_host_keeps_mails_auth_servers() -> None:
    config = MailConfig.model_validate(
        {
            "host": "imap.example.com",
            "auth_servers": ["mx.example.com"],
            "home": HOME,
            "work": {"host": "imap.example.com", "username": "me@work.example"},
        }
    )
    assert [m.trusted_auth_servers() for m in config.mailboxes()] == [{"mx.example.com"}] * 2
    cased = config.model_copy(update={"host": "IMAP.Example.com"})
    assert [m.trusted_auth_servers() for m in cased.mailboxes()] == [{"mx.example.com"}] * 2


def test_gmails_own_labels_make_a_thread_about_nothing() -> None:
    harness = mailbox(
        message(1, subject="Hello", thread="a", labels=["\\Inbox", "\\Starred"]),
        message(2, subject="Hi", thread="b", labels=["\\Inbox", "\\Starred"]),
    )
    harness.with_entities(
        [Entity(ref=Ref(kind="notes.todo", id="8"), title="clear the #inbox, #starred too")]
    )
    items = _items(harness)
    assert len(items) == 2  # each has #inbox, as does the other and the todo
    assert not any(d.startswith("about") for item in items for d in item.details)
    assert all(item.signals is not None and item.signals.importance == 0 for item in items)


def test_a_label_of_mine_or_the_subject_names_what_others_carry() -> None:
    todo = Entity(ref=Ref(kind="notes.todo", id="7"), title="take the #garbage-day bins out")
    labelled = mailbox(message(1, subject="Hello", labels=["\\Inbox", "Garbage Day"]))
    labelled.with_entities([todo])
    named = mailbox(message(1, subject="Garbage day moved", labels=["Garbage Day"]))
    named.with_entities([todo])
    for harness in (labelled, named):
        [item] = _items(harness)
        assert "about #garbage-day" in item.details


def test_an_unverified_reply_to_my_mail_lifts_nothing() -> None:
    harness = mailbox(message(1, replies_to_me=True, verified=False))
    [item] = _items(harness)
    assert "replies to me" not in item.badges
    assert item.signals is not None and item.signals.importance == 0


def test_a_message_id_shaped_like_one_given_to_mail_without_one_is_ignored(
    imap: Callable[..., FakeImap],
) -> None:
    imap([(4, "", headers(message_id="uid:5", references="<uid:5>"))])
    [found] = client.collect(_mailbox(), ME, NOW).messages
    assert (found.message_id, found.thread) == (None, "uid:4")
