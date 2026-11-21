"""The mail plugin: unread and flagged mail, so the people who matter get answered first."""

from typing import ClassVar

from move_forward.api import (
    API_VERSION,
    ChangeChecker,
    Changed,
    Collector,
    Context,
    ContextRequest,
    Entity,
    InputRequest,
    KindSpec,
    Migration,
    PageHandler,
    Panel,
    Plugin,
    Ref,
    RefreshRequest,
    Request,
    Response,
    attrs,
)
from move_forward.api.kinds import PERSON
from move_forward.plugins.mail import client
from move_forward.plugins.mail.config import MailConfig
from move_forward.plugins.mail.kinds import THREAD, MailPerson, ThreadAttrs
from move_forward.plugins.mail.models import MailSnapshot
from move_forward.plugins.mail.panels import OPEN_PAGE, link, message_url, panels, threads
from move_forward.plugins.mail.repository import MIGRATIONS, MessageRepository
from move_forward.plugins.mail.setup import INIT


def person_id(key: str) -> str:
    """The id of the `person` `key` names: `mail/<address>` for an address, the same whichever
    mail plugin publishes them, and apart from any code host's `<host>/<login>`; `mail/<login>`
    for me signing in to a mailbox with a bare login. `key` has no `/`."""
    return f"mail/{key}"


def password(ctx: Context[MailConfig]) -> str:
    """The mailbox's password (`ctx.token`, which says "no password" and how to give one when
    there's none)."""
    return ctx.token(secret="password")


class MailPlugin(
    Plugin[MailConfig, MailSnapshot], Collector[MailConfig, MailSnapshot], ChangeChecker[MailConfig]
):
    name = "mail"
    title = "Mail"
    init = INIT
    config_model = MailConfig
    api_version = API_VERSION
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {
        # Its title is a subject anyone can write: a #tag in it puts no tag on it.
        THREAD: KindSpec(ThreadAttrs, gone_means_done=True),
        PERSON: KindSpec(MailPerson),
    }
    """Threads are published while they have unread or flagged mail: one that's gone is done."""

    def collect(self, req: RefreshRequest[MailConfig]) -> MailSnapshot:
        """The mailbox's unread and flagged mail, newest first."""
        ctx = req.ctx
        return client.collect(ctx.config, password(ctx), ctx.email, ctx.now)

    def snapshots(self, req: ContextRequest[MailConfig]) -> MessageRepository:
        return MessageRepository(req.ctx.db)

    def change_check_seconds(self, req: ContextRequest[MailConfig]) -> int | None:
        """Every 120 seconds: the mailbox's STATUS, one cheap command."""
        return 120

    def changed(self, req: RefreshRequest[MailConfig]) -> Changed:
        """The mailbox's counts, as the fingerprint core compares with the last: they move when
        mail comes, goes or is read. One `STATUS`, without opening the mailbox."""
        ctx = req.ctx
        now = client.status(ctx.config, password(ctx))
        return Changed(fingerprint=now, reason="the mailbox changed")

    def entities(self, req: Request[MailConfig, MailSnapshot]) -> list[Entity]:
        found: list[Entity] = []
        people: dict[str, Entity] = {}
        me = req.ctx.config.username.strip()  # me, as the mailbox signs in
        # An address is me by it, with that email; a bare login (`jdoe`) is me by the login,
        # with no email to join me with elsewhere. A `/` can't go in a person's id
        # (`mail/<id>`), so such a username is no one, and the footer leaves it out.
        address = me.lower() if "@" in me else None
        if me and "/" not in me:
            people[address or me] = Entity(
                ref=Ref(kind=PERSON, id=person_id(address or me)),
                title=me,
                attrs=attrs(MailPerson(handle=me, email=address, me=True)),
            )
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

    def panels(self, req: Request[MailConfig, MailSnapshot]) -> list[Panel]:
        return panels(req.ctx.config, self.name, req.snapshot, req.graph)

    def pages(self) -> dict[str, PageHandler]:
        return {OPEN_PAGE: self._open}

    def _open(self, req: InputRequest[MailConfig]) -> Response:
        """Send the browser to a thread's latest message in the mail app, for a `message_url`
        the page won't link to itself (`message:`, `mid:`). Only a thread the mailbox has, so
        the link is always one the config made."""
        wanted = (req.page.query.get("thread") or [""])[0]
        latest = MessageRepository(req.ctx.db).latest(wanted)
        url = message_url(req.ctx.config, latest) if latest else None
        if url is None:
            return Response(body="No such message: it may have been read since.", status=404)
        return Response.redirect(url)
