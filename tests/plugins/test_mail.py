"""The mail plugin: reading a mailbox's headers over IMAP, and what it publishes and shows."""

import imaplib
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
        date=datetime(2026, 11, 7, 18, tzinfo=UTC),
    )


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
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, host: str, port: int, ssl_context: object, timeout: int) -> "FakeImap":
        self.calls.append(("connect", host, port, timeout))
        return self

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", user, password))
        if self.refuse:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b""]

    def select(self, mailbox: str, readonly: bool) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u, _, _ in self.messages)]
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
            (5, "", headers(message_id="old@acme.com")),
            (6, "\\Seen \\Flagged", headers(subject="Newer", message_id="new@acme.com")),
        ]
    )
    snapshot = client.collect(_config(since_days=7, max_messages=1), "me@work.com", NOW)
    assert [m.subject for m in snapshot.messages] == ["Newer"]  # the newest, at most one
    assert ("connect", "imap.gmail.com", 993, client.TIMEOUT_SECONDS) in server.calls
    assert ("login", ME, "app-password") in server.calls
    assert ("select", '"INBOX"', True) in server.calls  # read-only: EXAMINE
    assert ("uid", "SEARCH", "SINCE", "31-Oct-2026", "OR", "UNSEEN", "FLAGGED") in server.calls
    [fetch] = [c for c in server.calls if c[:2] == ("uid", "FETCH")]
    assert fetch[2] == "6" and "BODY.PEEK[HEADER.FIELDS (" in str(fetch[3])
    assert server.calls[-1] == ("logout",)


def test_a_refused_sign_in_says_what_to_do(imap: Callable[..., FakeImap]) -> None:
    imap([], refuse=True)
    with pytest.raises(SourceError, match="app password"):
        client.collect(_config(), "me@work.com", NOW)


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
    assert any("Alice waiting" in d for d in item.details)


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
    found = harness.page("open", {"thread": ["root@acme.com"]})
    assert isinstance(found, Response)
    assert (found.status, found.headers["Location"]) == (303, "message:%3Cm1@acme.com%3E")
    missing = harness.page("open", {"thread": ["gone@acme.com"]})
    assert isinstance(missing, Response) and missing.status == 404


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
