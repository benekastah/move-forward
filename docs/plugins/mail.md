# mail

Unread and flagged mail, by subject and sender, from any mailbox that speaks IMAP, so the people
who matter get answered first. Enabled by a `[mail]` section.

It reads the mailbox without changing it: it opens it read-only (`EXAMINE`) and fetches only
the headers it needs with `BODY.PEEK`, so nothing is marked read, and it never fetches a
message's body.

## Setup

Add a `[mail]` section with `host` and `username` (`mfd init` asks, and guesses Gmail's from
your email). It signs in over TLS with a password; for Gmail, make an app password at
https://myaccount.google.com/apppasswords (it needs 2-Step Verification), then store it with

```sh
mfd token set mail
```

which asks for it without showing it and keeps it in your system's secret store (the macOS
keychain, Windows' Credential Manager, or on Linux the desktop keyring through `secret-tool`)
under `keychain_service`, with your top-level `email` as the account. Or export
`MAIL_PASSWORD` (or the variable `token_env` names), which wins over the store.
`mfd token check` says where it was found.

A password is all it signs in with for now, so a mailbox that allows only OAuth (Microsoft 365)
can't be read yet.

## Config

`host` is the IMAP server (`imap.gmail.com`), and `port` (993) its port for IMAP over TLS.
`username` is who to sign in as, and also my address, with the top-level `email`, for telling
mail sent to me from mail I'm copied on. `mailbox` (`INBOX`) is the mailbox read, and
`sent_mailbox` where my sent mail is (empty: the one the server marks as sent), whose
Message-IDs and recipients alone are read, to know which mail answers me and whom I know:
someone I wrote to at least twice in the last `known_days` (90). `urgent_words` are words or
phrases in a subject that make a conversation from someone I know (verified) critical,
above other important mail, badged "urgent", and `low_words` ones that rank a conversation
with lists' mail, unless it replies to me or is from someone I know, a verified sender
either way (flagged mail still counts): whole words, ignoring case and how a phrase is spaced;
both empty by default. Mail older than `since_days` (14) is left out, flagged or not; at most
`max_messages` (200) unread are read, newest first, and every flagged one. `token_env` and `keychain_service` say where its
password is.

`auth_servers` names the servers whose `Authentication-Results` count: their first word, the
authserv-id (`mx.google.com` for Gmail, known without setting it; for another server, look in a
message's source). A sender counts as who `From` says only when the topmost such header, stamped
with one of those names, has one `dmarc` result and it passed for the `From` domain (an address
with more than one `@` never is). For a host it isn't known for, or with `auth_servers = []`,
no sender is verified, and their mail takes no contact type. Microsoft 365 and Outlook.com stamp
their results with no name, so their senders can't be verified.

`refresh_seconds` (900) is how often it reads the mailbox in the background. Every two minutes it
also asks the server for the mailbox's counts (one `STATUS`, without opening it), and reads it at
once when they've moved: mail came, went or was read.

`message_url` links each item to its message, with `{message_id}` (its Message-ID, without `<>`)
and `{username}` filled in, percent-encoded. `mfd init` sets Gmail's:

| Mail tool | `message_url` |
| --- | --- |
| Gmail | `https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}` |
| Apple Mail | `message:%3C{message_id}%3E` |
| Thunderbird | `mid:{message_id}` (where the system hands `mid:` links to it) |
| Outlook on the web | `https://outlook.office.com/mail/inbox` (the inbox: it has no link by Message-ID) |

It must be an `https://` link or start with `message:` or `mid:`; empty, the default, leaves mail
unlinked. The page links only to http(s), so an item with a `message:` or `mid:` link goes
through the plugin's `/mail/open` page, which sends the browser on to the mail app.

### Several mailboxes

Each sub-table of `[mail]` is a mailbox of its own, named by it: `[mail.home]` and `[mail.work]`
read two. What one leaves out, `[mail]` says for every one (`host`, `port`, `username`,
`mailbox`, `sent_mailbox`, `message_url`, `auth_servers`); `refresh_seconds`, `known_days`,
`since_days`, `max_messages` and the word lists are `[mail]`'s alone. With accounts, `[mail]` is
no mailbox itself: it can't have a `username` (move it to an account, `[mail.home]`, and set its
password with `mfd token set mail.home`). Only an account on `[mail]`'s `host` (whatever its
case) takes `[mail]`'s `auth_servers`, as which server's results count depends on the server;
the config is refused when `[mail]` sets them and no account takes them. An account's name
starts with a letter, then up to 29 lower-case letters, digits and `_`.

```toml
[mail]
host = "imap.gmail.com"
message_url = "https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}"

[mail.home]
username = "me@gmail.com"

[mail.work]
host = "imap.work.example"
username = "me@work.example"
message_url = "mid:{message_id}"
```

Each has its own password, never `[mail]`'s: `mfd token set mail.work` stores it under
`move-forward-mail-work`, or export `MAIL_PASSWORD_WORK` (or set the account's `token_env` and
`keychain_service`). A conversation is one item whichever mailboxes have it, linked by the
mailbox of its latest message, and a message in two (sent to both addresses) counts once, as the
first mailbox's, flagged, to me, replying to me, from someone I know or important if either copy
is. Only copies whose sender both servers verified merge, as anyone can write a `From` and a
Message-ID: any other is kept once per mailbox, in the same conversation, and counted once
among its unread. Nothing is merged within one mailbox. Each mailbox's sent mail says whom I
know and what answers me for its own mail. A mailbox that fails to sign in fails the refresh,
keeping what was read last.

## Publishes

`mail.thread` for each conversation with unread or flagged mail, by the Message-ID it started
with (`uid:<uid>` for a message with none, `uid:<account>:<uid>` in an account's mailbox),
pointing at the `person` who wrote its latest message and, when its sender is verified,
mentioning its subject (so a ticket key in it relates the two). Its Gmail labels, Gmail's own
(`#inbox`, `#starred`) and mine, are its tags, whoever sent it. A `#tag` in a subject puts no
tag on the thread (`tags_from_text=False`): anyone can write one. A subject still names tags in
the graph whoever sent it; only what mail says it's about, and how it ranks, waits for a
verified sender. That `person` is `mail/<address>`, with the address as its `handle` and
`email`, so core joins them with the same person in other tools, a CRM say, and ranks and badges
their mail by their contact type. Only a sender the receiving server vouched for is published
that way: one whose `From` passed DMARC (its one `dmarc` result), by the server's own
`Authentication-Results` (the topmost, stamped with a name in `auth_servers`; any below came
with the message). Anyone can write any `From`, and a forged one mustn't borrow a customer's
type. Others' mail is still listed, from whoever it says. See [entities.md](../entities.md).

A conversation is the Message-ID its first message named, which anyone replying can name.
So its flag (and the importance it gives) counts only flagged mail from whoever wrote its latest
message, and a verified one's flag only when the latest is verified too (an unverified
message's flag can still be claimed by an unverified reply giving the same `From`, as everywhere
when no sender can be verified).

A priority, pin or tag I put on a conversation stays with it, whoever replies, a known
limitation. A snooze or dismiss doesn't: any new reply brings it back, as it's kept by the
latest message. Only the first message's Message-ID names the conversation, so it's mostly
someone already in it, and what they gain is a place on my own page; from a sender the server
didn't verify, a tag its subject names doesn't make it about my work nor raise it further. Telling a newcomer from
someone already in the conversation would split real conversations.

When a message came is when the server received it (`INTERNALDATE`), not its `Date` header,
which says whatever the sender likes.

## Panels

- **Mail**: a conversation per item, newest first; those whose people matter more (flagged, or
  by contact type) come first, and lists, newsletters and automated mail (`List-Id`,
  `Precedence: bulk`, `Auto-Submitted`, and on Gmail its Promotions and Social, found with its
  `X-GM-RAW` search, unless its verified sender replies to me or is someone I know) last. Each
  says who it's from, how many messages are unread, whether it's flagged (or Gmail finds it
  important), and `cc` when I'm only copied.

## Act now

Every conversation but those from lists and automated mail (unless flagged), as Read, in minutes.
Mail sent to me has its sender waiting since the server received it. A conversation is
important when it's flagged, when its subject names one of my open tickets, or a `#tag` of
several words something open carries (see the README's Tags), and its sender is verified
(shown as "about APP-12 …" or "about #garbage-day"; a priority I set on that ticket counts for
it too; a one-word tag, like `#invoice`, any stranger may write, so it's shown but raises
nothing; a tag or label I put on the mail counts too, while something else open has it and its
latest sender is verified, since anyone can reply into a conversation, but not Gmail's own
labels, `#inbox` or `#starred`, which say nothing of what it's about), when its latest message,
from a verified sender (anyone can name a Message-ID of mine), answers mail I sent (the newest 1000 within `known_days`; its
`In-Reply-To`, else the last of its `References`, names one of my sent Message-IDs; badged
"replies to me"; if my sent mail can't be read, nothing counts as replying), when it's from
someone I know (badged "known"; only a verified sender), or when Gmail labels it Important
(Gmail's `X-GM-LABELS`, read only from a server offering them); not for lists and automated
mail, but flagged. One from someone I know whose subject has one of `urgent_words` is
critical (badged "urgent"); one whose subject has one of `low_words` counts as lists' mail,
unless its verified sender replies to me or is someone I know. The sender's contact type, or how much a
tool says they matter, can raise it further.

## Pages

- `/mail/open?thread=<id>`: sends the browser to the conversation's latest message with
  `message_url`, for a mail app's link; only for a conversation the mailbox has.
