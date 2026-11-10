"""The mail plugin: unread and flagged mail, so the people who matter get answered first."""

from collections.abc import Mapping
from typing import ClassVar

from ...api import (
    API_VERSION,
    Asker,
    Changed,
    ChangedRequest,
    CollectRequest,
    Context,
    EntitiesRequest,
    Entity,
    Identity,
    IdentityRequest,
    InitSetup,
    KindSpec,
    LoadRequest,
    Migration,
    PageHandler,
    PageRequest,
    Panel,
    PanelsRequest,
    Plugin,
    Ref,
    Response,
    StoreRequest,
    attrs,
)
from ...api.keychain import TokenSettings
from ...api.kinds import PERSON
from . import client
from .config import KNOWN_AUTH_SERVERS, Mailbox, MailConfig
from .kinds import THREAD, MailPerson, ThreadAttrs
from .models import MailSnapshot, Message
from .panels import OPEN_PAGE, link, message_url, panels, threads
from .repository import MIGRATIONS, MessageRepository
from .setup import EXAMPLE, TABLES, questions


def person_id(email: str) -> str:
    """The id of the `person` with address `email`: `mail/<address>`, the same whichever mail
    plugin publishes them, and apart from any code host's `<host>/<login>`."""
    return f"mail/{email}"


def password(ctx: Context[MailConfig], mailbox: Mailbox) -> str:
    """`mailbox`'s password (`ctx.token`, which says "no password" and how to give one when
    there's none)."""
    return ctx.token(mailbox.name, secret="password")


def merged(read: list[Message], more: list[Message]) -> list[Message]:
    """`read`, from the mailboxes before, with another mailbox's messages: a copy of one already
    read (the same Message-ID from the same sender, each copy's sender verified) adds only
    what's about me there, flagged, to me, replying to me, from someone I know or important, to
    the first copy. Only verified copies merge, as anyone can write a `From` and a Message-ID:
    unverified, a message sent to two of my addresses is kept twice, in one conversation, as the
    copies name the same one (but a message with no Message-ID, which is its mailbox's alone).
    Within a mailbox nothing is merged."""
    first: dict[tuple[str, str | None], int] = {
        (m.message_id, m.sender_email): i for i, m in enumerate(read) if m.message_id and m.verified
    }
    found = list(read)
    taken: set[int] = set()  # a mailbox has one copy of a message: more claim to be it
    for message in more:
        i = first.get((message.message_id, message.sender_email)) if message.message_id else None
        if not message.verified or i is None or i in taken:
            found.append(message)
            continue
        taken.add(i)
        copy = found[i]
        found[i] = copy.model_copy(
            update={
                "flagged": copy.flagged or message.flagged,
                "to_me": copy.to_me or message.to_me,
                "replies_to_me": copy.replies_to_me or message.replies_to_me,
                "known": copy.known or message.known,
                "important": copy.important or message.important,
            }
        )
    return found


class MailPlugin(Plugin[MailConfig, MailSnapshot]):
    name = "mail"
    title = "Mail"
    init_tables = TABLES
    init_example = EXAMPLE
    config_model = MailConfig
    api_version = API_VERSION
    snapshot_model = MailSnapshot
    locked_settings: ClassVar[frozenset[str]] = frozenset(
        {"host", "port", "message_url", "auth_servers"}
    )
    """Where its passwords are sent, where its items link (with my username in the link), and
    whose word decides a sender is who they claim."""
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        # Its title is a subject anyone can write: a #tag in it puts no tag on it.
        THREAD: KindSpec(ThreadAttrs, gone_means_done=True, tags_from_text=False),
        PERSON: KindSpec(MailPerson),
    }
    """Threads are published while they have unread or flagged mail: one that's gone is done."""
    change_check_seconds: ClassVar[int | None] = 120

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def init_shown(self, section: Mapping[str, object]) -> dict[str, object]:
        """`auth_servers` as the host's known ones (Gmail's), or `[]` for another server,
        which reads the same as unset there: uncommenting it changes nothing, and its comment
        says what to put in."""
        host = str(section.get("host", "")).lower()
        return {"auth_servers": KNOWN_AUTH_SERVERS.get(host, [])}

    def tokens(self, config: MailConfig) -> dict[str, TokenSettings]:
        """Each mailbox's password, by its account's name (`mail.work`), or `[mail]`'s own."""
        return {m.name: m for m in config.mailboxes()}

    def collect(self, req: CollectRequest[MailConfig]) -> MailSnapshot:
        """Every mailbox's unread and flagged mail, newest first. A conversation is one whichever
        mailboxes have it, and a message sent to two of my addresses is one (`merged`)."""
        ctx = req.ctx
        messages: list[Message] = []
        for mailbox in ctx.config.mailboxes():
            found = client.collect(mailbox, password(ctx, mailbox), ctx.email, ctx.now)
            messages = merged(messages, found.messages)
        return MailSnapshot(messages=sorted(messages, key=lambda m: (m.date, m.uid), reverse=True))

    def store(self, req: StoreRequest[MailConfig, MailSnapshot]) -> None:
        MessageRepository(req.ctx.db).replace(req.snapshot)

    def load(self, req: LoadRequest[MailConfig]) -> MailSnapshot | None:
        return MessageRepository(req.ctx.db).load()

    def changed(self, req: ChangedRequest[MailConfig]) -> Changed:
        """The mailboxes' counts, as the fingerprint core compares with the last: they move
        when mail comes, goes or is read. One `STATUS` each, without opening the mailbox."""
        ctx = req.ctx
        now = "\n".join(client.status(m, password(ctx, m)) for m in ctx.config.mailboxes())
        return Changed(reason="the mailbox changed", fingerprint=now)

    def identity(self, req: IdentityRequest[MailConfig, MailSnapshot]) -> Identity | None:
        return Identity(
            tool=self.title, login=", ".join(m.username for m in req.ctx.config.mailboxes())
        )

    def entities(self, req: EntitiesRequest[MailConfig, MailSnapshot]) -> list[Entity]:
        found: list[Entity] = []
        people: dict[str, Entity] = {}
        for thread in threads(req.snapshot):
            latest = thread.latest
            email = latest.sender_email
            refs: list[Ref] = []
            # Only a sender the server vouched for is a person: core gives people their
            # contact type, which a forged `From` mustn't borrow.
            # A `/` can't go in a person's id (`mail/<address>`): such a sender is no one.
            if email and latest.verified and "/" not in email:
                person = people.setdefault(
                    email,
                    Entity(
                        ref=Ref(kind=PERSON, id=person_id(email)),
                        title=latest.sender_name,
                        attrs=attrs(MailPerson(handle=email, email=email)),
                    ),
                )
                refs.append(person.ref)
            found.append(
                Entity(
                    ref=Ref(kind=THREAD, id=thread.id),
                    title=latest.subject,
                    url=link(req.ctx.config, self.name, thread),
                    state="flagged" if thread.flagged else "unread",
                    refs=refs,
                    # What it's about only for a sender the server vouched for: naming my ticket
                    # makes mail important, says "about" it and takes its priority, which a
                    # forged From mustn't get by typing a key.
                    mentions=[latest.subject] if latest.verified else [],
                    # Its sender wrote the subject: a ticket I iced that it names mustn't hide
                    # their mail from me.
                    mentions_hide=False,
                    # Its labels are its tags, whoever sent it: mine, and Gmail's own (#inbox).
                    tags=list(dict.fromkeys(label for m in thread.messages for label in m.labels)),
                    attrs=attrs(
                        ThreadAttrs(
                            subject=latest.subject,
                            sender_email=email,
                            unread=thread.unread,
                            flagged=thread.flagged,
                            bulk=latest.bulk,
                            to_me=latest.to_me,
                            latest=latest.date.isoformat(),
                        )
                    ),
                )
            )
        return [*found, *people.values()]

    def panels(self, req: PanelsRequest[MailConfig, MailSnapshot]) -> list[Panel]:
        return panels(req.ctx.config, self.name, req.snapshot, req.graph)

    def pages(self) -> dict[str, PageHandler]:
        return {OPEN_PAGE: self._open}

    def _open(self, req: PageRequest[MailConfig]) -> Response:
        """Send the browser to a thread's latest message in the mail app, for a `message_url`
        the page won't link to itself (`message:`, `mid:`). Only a thread the mailbox has, so
        the link is always one the config made."""
        wanted = (req.query.get("thread") or [""])[0]
        latest = MessageRepository(req.ctx.db).latest(wanted)
        url = message_url(req.ctx.config, latest) if latest else None
        if url is None:
            return Response(body="No such message: it may have been read since.", status=404)
        return Response.redirect(url)
