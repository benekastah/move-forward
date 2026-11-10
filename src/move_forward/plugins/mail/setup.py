"""`mfd init`'s questions for `[mail]`."""

from ...api import Asker, InitSetup
from .config import GMAIL_MESSAGE_URL

TABLES = """\
## Several mailboxes instead: a table each, saying its own host, port, username, mailbox,
## sent_mailbox, message_url and auth_servers (each with the same default as [mail]'s), and
## its own token_env and keychain_service, and none of them in [mail]. Each one's password:
## `mfd token set mail.<name>`, or $MAIL_PASSWORD_<NAME> (`mail.home`, $MAIL_PASSWORD_HOME).
##
## [mail.home]
## host = "imap.gmail.com"
## username = "me@example.com"
## message_url = "https://mail.google.com/mail/u/{username}/#search/rfc822msgid:{message_id}"
##
## [mail.work]
## host = "imap.work.example"
## port = 993
## username = "me@work.example"
## mailbox = "INBOX"
## sent_mailbox = "Sent"
## message_url = "mid:{message_id}"
## auth_servers = ["mx.work.example"]
## token_env = "MAIL_PASSWORD_WORK"
## keychain_service = "move-forward-mail-work"
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
