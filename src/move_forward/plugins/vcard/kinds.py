"""The entity kinds vcard publishes, with their attributes; see `docs/entities.md`."""

from pydantic import Field

from ...api.kinds import Person


class VcardPerson(Person):
    """A `person`: someone in the address book, by `vcard/<address>`; `handle` and `email`
    are their address, `importance` and `contact_type` what their groups give."""

    groups: list[str] = Field(default_factory=list[str])
    """The address book's groups they're in."""
