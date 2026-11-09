"""The mail panel: pure functions from the snapshot to panels."""

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from ...api import Badge, Band, Graph, Item, Panel, Ref, Signals
from ...api.kinds import TAG
from ...api.tags import handle as tag_handle
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
        """Its unread messages, a message in two of my mailboxes counted once."""
        unread = [m for m in self.messages if not m.seen]
        return len({m.message_id for m in unread if m.message_id}) + sum(
            not m.message_id for m in unread
        )

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
    """The link its mailbox's config makes to `message`, its Message-ID and the username
    percent-encoded; None without a template (or a mailbox: one since taken out of the config),
    or for a message with no Message-ID when the template needs one. `@` stays as it is: it's
    safe in a path and a fragment, and mail apps' links expect it so."""
    mailbox = config.mailbox_named(message.account)
    template = mailbox.message_url if mailbox else ""
    if mailbox is None or not template:
        return None
    if message.message_id is None and "{message_id}" in template:
        return None
    return template.replace("{message_id}", quote(message.message_id or "", safe="@")).replace(
        "{username}", quote(mailbox.username, safe="@")
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
    a verified sender: see `MailPlugin.entities`), by title: mail about my work is worth
    reading before the rest."""
    return [
        entity.title
        for unit in graph.units(Ref(kind=THREAD, id=thread.id))
        if (entity := graph.get(unit.kind, unit.id)) is not None and entity.lifecycle == "open"
    ]


def named_tags(graph: Graph, thread: Thread) -> list[str]:
    """The `#tags` a conversation is about, by handle, when its latest sender is verified: those
    its subject names, and those I put on it, each while something else open carries it, so a
    tag only it has isn't news. The labels I or my filters put on it count, but not Gmail's own
    (`\\Inbox`, `\\Starred`), which say nothing of what it's about and are on most mail. Anyone
    can write a subject, and reply into a conversation by naming it (`References`), taking on
    what I put on it: an unverified sender gets none."""
    ref = Ref(kind=THREAD, id=thread.id)
    entity = graph.get(THREAD, thread.id)
    if entity is None or not thread.latest.verified:
        return []
    gmails = {tag_handle(label) for m in thread.messages for label in m.labels if label[:1] == "\\"}
    named = [r.id for r in entity.refs if r.kind == TAG]
    mine = [
        tag.ref.id for tag in graph.of_kind(TAG) if ref in tag.refs and tag.ref.id not in gmails
    ]
    return [
        handle
        for handle in dict.fromkeys([*named, *mine])
        if (tag := graph.get(TAG, handle)) is not None
        and any(
            (carrier := graph.get(r.kind, r.id)) is not None and carrier.lifecycle == "open"
            for r in tag.refs
            if r != ref  # not the open thread itself
        )
    ]


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
    tagged = named_tags(graph, thread)
    # Someone I write to, but only one the server vouched for: a forged From mustn't borrow it.
    known = latest.known and latest.verified
    # Replying to me only counts from a sender the server vouched for: anyone can write an
    # `In-Reply-To` naming one of my Message-IDs, which everyone I wrote to has.
    replies = latest.replies_to_me and latest.verified
    # Low words push mail down, but not a reply to me or mail from someone I know, as Gmail's
    # categories don't; urgent words lift only someone I know: anyone can write "urgent".
    low = says(config.low_words, latest.subject) and not (replies or known)
    bulk = latest.bulk or low
    urgent = known and not bulk and says(config.urgent_words, latest.subject)
    # A one-word tag ("invoice") is a word any stranger may write, so it's said but lifts
    # nothing (someone I know is lifted anyway); one of several words ("garbage day") is hard
    # to hit by chance.
    tag_matters = any("-" in tag for tag in tagged)
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
    if replies and not bulk:
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
        details=[
            f"from {who}",
            *(f"about {title}" for title in work),
            *(f"about #{tag}" for tag in tagged),
        ],
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
            or ((latest.important or replies or work or tag_matters or known) and not bulk)
            else 0,
            waiting=[who] if person and latest.to_me else [],
            waiting_since=latest.date if person and latest.to_me else None,
        ),
    )


def _listed(names: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


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
            description=f"Unread and flagged mail in {_listed([m.shown for m in config.mailboxes()])}"
            ", newest first; lists and automated mail last.",
            items=[_item(config, plugin, thread, graph) for thread in threads(snapshot)],
            sort_by_importance=True,
            act_now=True,
        )
    ]
