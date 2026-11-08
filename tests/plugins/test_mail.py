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
from move_forward.graph import Entity, Ref, attrs
from move_forward.kinds import PERSON, Person
from move_forward.plugin import ChangedRequest, Response, SourceError
from move_forward.plugins.mail import client
from move_forward.plugins.mail.config import GMAIL_MESSAGE_URL, MailConfig
from move_forward.plugins.mail.kinds import THREAD
from move_forward.plugins.mail.models import MailSnapshot, Message
from move_forward.plugins.mail.plugin import MailPlugin
from move_forward.plugins.mail.repository import MessageRepository
from move_forward.plugins.mail.setup import questions
from move_forward.repositories import ContactRepository
from move_forward.testing import Harness

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
ME = "me@gmail.com"
SECTION = {"host": "imap.gmail.com", "username": ME, "message_url": GMAIL_MESSAGE_URL}


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
    assert _config().trusted_auth_servers() == {"mx.google.com"}
    assert _config(host="imap.example.com").trusted_auth_servers() == set()
    assert _config(
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
        return "OK", [b"3"]

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.calls.append(("uid", command, *args))
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
    snapshot = client.collect(_config(since_days=7, max_messages=1), "me@work.com", NOW)
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


def test_a_refused_sign_in_says_what_to_do(imap: Callable[..., FakeImap]) -> None:
    imap([], refuse=True)
    with pytest.raises(SourceError, match="app password"):
        client.collect(_config(), "me@work.com", NOW)


def test_a_password_imap_cant_send_says_so(
    imap: Callable[..., FakeImap], monkeypatch: pytest.MonkeyPatch
) -> None:
    imap([])
    monkeypatch.setenv("MAIL_PASSWORD", "pässword")
    with pytest.raises(SourceError, match="ASCII"):
        client.collect(_config(), ME, NOW)


def test_no_password_says_how_to_set_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAIL_PASSWORD", raising=False)

    def nothing_stored(service: str, account: str) -> None:
        return None

    monkeypatch.setattr(client.keychain, "read", nothing_stored)
    with pytest.raises(SourceError, match="mfd token set mail"):
        client.collect(_config(), "me@work.com", NOW)


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
    snapshot = client.collect(_config(), ME, NOW)
    fetches = [c[2] for c in server.calls if c[:2] == ("uid", "FETCH")]
    assert fetches == ["1,2", "3,4", "5"] and len(snapshot.messages) == 5


def test_gmails_important_label_is_read() -> None:
    meta = b'1 (UID 7 X-GM-LABELS ("\\\\Important" "\\\\Inbox") FLAGS ())'
    found = client.message(meta, headers(), {ME})
    assert found is not None and found.important
    plain = client.message(b'1 (UID 7 X-GM-LABELS ("\\\\Inbox") FLAGS ())', headers(), {ME})
    assert plain is not None and not plain.important


def test_labels_are_asked_for_only_from_gmail(imap: Callable[..., FakeImap]) -> None:
    server = imap([(1, "", headers())])
    client.collect(_config(), ME, NOW)
    assert "X-GM-LABELS" not in str([c for c in server.calls if c[:2] == ("uid", "FETCH")])
    server = imap([(1, "", headers())])
    server.capabilities = ("IMAP4REV1", client.GMAIL)
    client.collect(_config(), ME, NOW)
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
    assert next(i.title for i in _items(harness)) == "Contract"
