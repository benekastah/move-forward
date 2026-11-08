"""The vcard plugin's data: the address book's people, by address."""

from pydantic import Field

from ...api import Model


class Contact(Model):
    """Someone in the address book, by one of their addresses: a card with several addresses
    is one of these for each."""

    email: str
    """Lower-case."""
    name: str
    """Their name on the card, else their address."""
    groups: list[str] = Field(default_factory=list[str])
    """The groups they're in, as the address book names them."""


class VcardSnapshot(Model):
    """Everyone in the address book with an address, in the order the files list them."""

    contacts: list[Contact]
