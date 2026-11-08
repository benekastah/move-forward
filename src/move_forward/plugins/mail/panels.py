"""The mail panel: pure functions from the snapshot to panels."""

from dataclasses import dataclass
from urllib.parse import quote

from ...api import Badge, Band, Item, Panel, Ref, Signals
from .config import APP_SCHEMES, MailConfig
from .kinds import THREAD
from .models import MailSnapshot, Message

OPEN_PAGE = "open"


@dataclass(frozen=True)
class Thread:
    """A conversation's unread and flagged messages, newest first."""

    id: str
    messages: list[Message]

    @property
    def latest(self) -> Message:
        return self.messages[0]

    @property
    def unread(self) -> int:
        return sum(not m.seen for m in self.messages)

    @property
    def flagged(self) -> bool:
        """One of its messages from whoever wrote the latest is flagged: anyone can reply into
        a conversation by naming it, and mustn't wear a flag I put on someone else's mail."""
        latest = self.latest
        if latest.sender_email is None:
            return latest.flagged
        # A verified message's flag counts only for a verified latest one: `From` is the
        # sender's to write, so it alone proves nothing.
        return any(
            m.flagged
            for m in self.messages
            if m.sender_email == latest.sender_email and (latest.verified or not m.verified)
        )


def threads(snapshot: MailSnapshot | None) -> list[Thread]:
    """The snapshot's messages by conversation, the most recent conversation first."""
    found: dict[str, list[Message]] = {}
    for message in snapshot.messages if snapshot else []:
        found.setdefault(message.thread, []).append(message)
    return [Thread(id, messages) for id, messages in found.items()]


def message_url(config: MailConfig, message: Message) -> str | None:
    """The configured link to `message`, its Message-ID and the username percent-encoded;
    None without a template, or for a message with no Message-ID when the template needs
    one. `@` stays as it is: it's safe in a path and a fragment, and mail apps' links expect
    it so."""
    template = config.message_url
    if not template or (message.message_id is None and "{message_id}" in template):
        return None
    return template.replace("{message_id}", quote(message.message_id or "", safe="@")).replace(
        "{username}", quote(config.username, safe="@")
    )


def link(config: MailConfig, plugin: str, thread: Thread) -> str | None:
    """Where a thread's item links: the message itself for an https:// link, else (a mail
    app's scheme, which the page won't link to) the plugin's page that sends the browser on."""
    url = message_url(config, thread.latest)
    if url is None or not url.startswith(APP_SCHEMES):
        return url
    return f"/{plugin}/{OPEN_PAGE}?thread={quote(thread.id, safe='')}"


def _item(config: MailConfig, plugin: str, thread: Thread) -> Item:
    latest = thread.latest
    who = latest.sender_name
    if latest.sender_email and latest.sender_email != who.lower():
        who = f"{who} <{latest.sender_email}>"
    badges: list[str | Badge] = []
    if thread.unread > 1:
        badges.append(f"{thread.unread} unread")
    if thread.flagged:
        badges.append(Badge(text="flagged", tone="warn"))
    if latest.important and not thread.flagged:
        badges.append("important")
    if not latest.to_me and not latest.bulk:
        badges.append("cc")
    person = not latest.bulk
    return Item(
        title=latest.subject,
        url=link(config, plugin, thread),
        subjects=[Ref(kind=THREAD, id=thread.id)],
        badges=badges,
        details=[f"from {who}"],
        rank=int(latest.date.timestamp()),
        deferred=latest.bulk and not thread.flagged,
        fingerprint=latest.message_id or str(latest.uid),
        signals=Signals(
            verb="Read",
            task="read",
            effort="minutes",
            importance=1 if thread.flagged or (latest.important and not latest.bulk) else 0,
            waiting=[who] if person and latest.to_me else [],
            waiting_since=latest.date if person and latest.to_me else None,
        ),
    )


def panels(config: MailConfig, plugin: str, snapshot: MailSnapshot | None) -> list[Panel]:
    """Mail: a conversation per item, the newest first, those from people who matter more
    (flagged, or by contact type) first of all; lists and automated mail last."""
    return [
        Panel(
            key="inbox",
            priority=Band.REVIEWS + 5,
            title="Mail",
            description=f"Unread and flagged mail in {config.mailbox}, newest first; lists and "
            "automated mail last.",
            items=[_item(config, plugin, thread) for thread in threads(snapshot)],
            sort_by_importance=True,
            act_now=True,
        )
    ]
