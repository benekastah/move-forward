"""The mail panel: pure functions from the snapshot to panels."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from ...api import Badge, Band, Graph, Item, Panel, Ref, Signals
from ...api.kinds import TAG
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


def about(graph: Graph, thread: Thread) -> list[str]:
    """The open units of work a conversation is about (one of my tickets its subject names, for
    a verified sender: see `MailPlugin.entities`), by title, then, for a verified sender, the
    `#tags` its subject names that something else open carries: mail about my work is worth
    reading before the rest."""
    ref = Ref(kind=THREAD, id=thread.id)
    units = [
        entity.title
        for unit in graph.units(ref)
        if (entity := graph.get(unit.kind, unit.id)) is not None and entity.lifecycle == "open"
    ]
    if not thread.latest.verified:
        return units  # anyone can write a subject: it names a tag only for one vouched for
    tagged = [
        tag.title
        for tag in graph.related(ref, TAG)
        if any(
            (carrier := graph.get(r.kind, r.id)) is not None and carrier.lifecycle == "open"
            for r in tag.refs
            if r != ref
        )
    ]
    return [*units, *tagged]


def says(words: list[str], subject: str) -> bool:
    """Whether `subject` has one of `words` (or phrases) as whole words, ignoring case, however
    the words of a phrase are spaced, and however its accents are encoded (NFC or NFD)."""
    subject = unicodedata.normalize("NFC", subject)
    return any(
        re.search(rf"(?<!\w){r'\s+'.join(map(re.escape, parts))}(?!\w)", subject, re.IGNORECASE)
        for word in words
        if (parts := unicodedata.normalize("NFC", word).split())
    )


def _item(config: MailConfig, plugin: str, thread: Thread, graph: Graph) -> Item:
    latest = thread.latest
    work = about(graph, thread)
    # Someone I write to, but only one the server vouched for: a forged From mustn't borrow it.
    known = latest.known and latest.verified
    # Low words push mail down, but not a reply to me or mail from someone I know, as Gmail's
    # categories don't; urgent words lift only someone I know: anyone can write "urgent".
    low = says(config.low_words, latest.subject) and not (latest.replies_to_me or known)
    bulk = latest.bulk or low
    urgent = known and not bulk and says(config.urgent_words, latest.subject)
    who = latest.sender_name
    if latest.sender_email and latest.sender_email != who.lower():
        who = f"{who} <{latest.sender_email}>"
    badges: list[str | Badge] = []
    if thread.unread > 1:
        badges.append(f"{thread.unread} unread")
    if thread.flagged:
        badges.append(Badge(text="flagged", tone="warn"))
    if urgent:
        badges.append(Badge(text="urgent", tone="warn"))
    if latest.replies_to_me and not bulk:
        badges.append("replies to me")
    elif known and not bulk:
        badges.append("known")
    elif latest.important and not thread.flagged and not bulk:
        badges.append("important")
    if not latest.to_me and not bulk:
        badges.append("cc")
    person = not bulk
    return Item(
        title=latest.subject,
        url=link(config, plugin, thread),
        subjects=[Ref(kind=THREAD, id=thread.id)],
        badges=badges,
        details=[f"from {who}", *(f"about {title}" for title in work)],
        rank=int(latest.date.timestamp()),
        deferred=bulk and not thread.flagged,
        fingerprint=latest.message_id or str(latest.uid),
        signals=Signals(
            verb="Read",
            task="read",
            effort="minutes",
            importance=2
            if urgent
            else 1
            if thread.flagged
            or ((latest.important or latest.replies_to_me or work or known) and not bulk)
            else 0,
            waiting=[who] if person and latest.to_me else [],
            waiting_since=latest.date if person and latest.to_me else None,
        ),
    )


def panels(
    config: MailConfig, plugin: str, snapshot: MailSnapshot | None, graph: Graph
) -> list[Panel]:
    """Mail: a conversation per item, the newest first, those from people who matter more
    (flagged, or by contact type) first of all; lists and automated mail last."""
    return [
        Panel(
            key="inbox",
            priority=Band.REVIEWS + 5,
            title="Mail",
            description=f"Unread and flagged mail in {config.mailbox}, newest first; lists and "
            "automated mail last.",
            items=[_item(config, plugin, thread, graph) for thread in threads(snapshot)],
            sort_by_importance=True,
            act_now=True,
        )
    ]
