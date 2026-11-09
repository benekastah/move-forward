"""`mfd init`'s questions for `[mail]`."""

from ...api import Asker, InitSetup
from .config import GMAIL_MESSAGE_URL

TEMPLATE = """\
# refresh_seconds = 900                 # how often it reads the mailbox; it also checks for changes every couple of minutes
# port = 993                            # IMAP over TLS
# mailbox = "INBOX"
# sent_mailbox = ""                     # where my sent mail is, to know what answers me; empty: the one the server marks
# known_days = 90                       # my sent mail read this far back: whom I know, and what replies to me
# urgent_words = []                     # in a subject, critical (only from someone I know, verified), e.g. ["outage"]
# low_words = []                        # in a subject, ranked with lists' mail (not from someone I know), e.g. ["digest", "FYI"]
# since_days = 14                       # older mail is left out, flagged or not
# max_messages = 200                    # the most unread read, newest first (flagged: all)
# message_url = ""                      # a link to a message: {message_id}, {username}; e.g. Apple Mail's "message:%3C{message_id}%3E"
# auth_servers = ["mx.google.com"]      # who stamps Authentication-Results; known for Gmail, else from a message's source
# token_env = "MAIL_PASSWORD"           # else the system's secret store, for your email (`mfd token set mail`)
# keychain_service = "move-forward-mail"
## Several mailboxes instead: a table each, saying its own host, port, username, mailbox,
## sent_mailbox, message_url and auth_servers (each with its default above), and none in [mail]
## [mail.home]
## host = "imap.gmail.com"
## username = "me@example.com"          # its password: `mfd token set mail.home`, or $MAIL_PASSWORD_HOME
## message_url = "https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}"
## [mail.work]
## host = "imap.work.example"
## port = 993
## username = "me@work.example"         # its password: `mfd token set mail.work`, or $MAIL_PASSWORD_WORK
## mailbox = "INBOX"
## sent_mailbox = "Sent"
## message_url = "mid:{message_id}"
## auth_servers = ["mx.work.example"]
"""
EXAMPLE = '# host = "imap.gmail.com"\n# username = "me@example.com"\n'
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def _required(answer: str) -> str:
    answer = answer.strip()
    if not answer:
        raise ValueError("required")
    return answer


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    """The server and who to sign in as, guessed from my email; a message link for Gmail."""
    domain = setup.email.rpartition("@")[2].lower()
    gmail = domain in GMAIL_DOMAINS
    host = ask("IMAP server", "imap.gmail.com" if gmail else f"imap.{domain}", _required)
    username = ask("Sign in as", setup.email, _required)
    found: dict[str, object] = {"host": host, "username": username}
    if host == "imap.gmail.com":
        found["message_url"] = GMAIL_MESSAGE_URL
    return found
