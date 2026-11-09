"""The mail plugin: unread and flagged mail, so the people who matter get answered first."""

from typing import ClassVar

from ...api import (
    API_VERSION,
    Asker,
    Changed,
    ChangedRequest,
    CollectRequest,
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
from .config import MailConfig
from .kinds import THREAD, MailPerson, ThreadAttrs
from .models import MailSnapshot, Message
from .panels import OPEN_PAGE, link, message_url, panels, threads
from .repository import MIGRATIONS, MessageRepository
from .setup import EXAMPLE, TEMPLATE, questions

STATUS = "status"
"""The kv key of the mailbox's status at the last look, for `changed`."""


def person_id(email: str) -> str:
    """The id of the `person` with address `email`: `mail/<address>`, the same whichever mail
    plugin publishes them, and apart from any code host's `<host>/<login>`."""
    return f"mail/{email}"


class MailPlugin(Plugin[MailConfig, MailSnapshot]):
    name = "mail"
    title = "Mail"
    init_template = TEMPLATE
    init_example = EXAMPLE
    config_model = MailConfig
    api_version = API_VERSION
    snapshot_model = MailSnapshot
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        # Its title is a subject anyone can write: a #tag in it puts no tag on it.
        THREAD: KindSpec(ThreadAttrs, gone_means_done=True, tags_from_text=False),
        PERSON: KindSpec(MailPerson),
    }
    """Threads are published while they have unread or flagged mail: one that's gone is done."""
    change_check_seconds: ClassVar[int | None] = 120

    def init_suggested(self) -> bool:
        """Only when asked for: it needs a mailbox and its password."""
        return False

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def tokens(self, config: MailConfig) -> dict[str, TokenSettings]:
        """Each mailbox's password, by its account's name (`mail.work`), or `[mail]`'s own."""
        return {m.name: m for m in config.mailboxes()}

    def collect(self, req: CollectRequest[MailConfig]) -> MailSnapshot:
        """Every mailbox's unread and flagged mail, newest first. A conversation is one whichever
        mailboxes have it; a message in several (sent to two of my addresses) is the first's
        copy, flagged if any is."""
        ctx = req.ctx
        found: dict[str, Message] = {}
        alone: list[Message] = []  # no Message-ID to know a copy by
        for mailbox in ctx.config.mailboxes():
            for message in client.collect(mailbox, ctx.email, ctx.now).messages:
                if message.message_id is None:
                    alone.append(message)
                elif (first := found.get(message.message_id)) is None:
                    found[message.message_id] = message
                elif message.flagged and not first.flagged:
                    found[message.message_id] = first.model_copy(update={"flagged": True})
        messages = [*found.values(), *alone]
        return MailSnapshot(messages=sorted(messages, key=lambda m: (m.date, m.uid), reverse=True))

    def store(self, req: StoreRequest[MailConfig, MailSnapshot]) -> None:
        MessageRepository(req.ctx.db).replace(req.snapshot)

    def load(self, req: LoadRequest[MailConfig]) -> MailSnapshot | None:
        return MessageRepository(req.ctx.db).load()

    def changed(self, req: ChangedRequest[MailConfig]) -> Changed:
        """Whether a mailbox's counts moved since the last look: mail came, went or was
        read. One `STATUS` each, without opening the mailbox."""
        ctx = req.ctx
        now = "\n".join(client.status(m, ctx.email) for m in ctx.config.mailboxes())
        before = ctx.kv.get(STATUS)
        ctx.kv.set(STATUS, now)
        return Changed(before is not None and before != now, "the mailbox changed")

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
