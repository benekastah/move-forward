"""Who people are to me: a contact type (customer, partner, ...), and how much they matter.

A type comes from me, on an address or a whole domain (`contacts`), or from a tool that knows
(`Person.contact_type`, say a CRM's); a tool can also say how much they matter outright
(`Person.importance`: a CRM's VIP, a starred contact). Mine wins over both. It counts for all of
a person's entities, since `Graph.same_person` joins them by email, and for the items about any
of them: core ranks those and badges them, so a plugin needs only to publish the person and
point at them.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, field_validator

from . import act_now
from .domain import Model
from .graph import Graph, Ref
from .kinds import PERSON, personal_email
from .page import Action, Item, ItemAdditions
from .strict import Strict

DEFAULT_CONTACT_TYPES = {"customer": 1, "partner": 1, "vendor": 0}
CONTACT_ACTION = f"{act_now.ACTIONS}/contact"
CLEAR_CONTACT_ACTION = f"{act_now.ACTIONS}/contact_clear"
_CONTACT_TYPE = re.compile(r"[a-z][a-z0-9 _-]{0,39}")
_DOMAIN = re.compile(r"@[^@\s]+\.[^@\s]+")
PUBLIC_DOMAINS = frozenset(
    {
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "googlemail.com",
        "hey.com",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "users.noreply.github.com",
        "yahoo.com",
    }
)
"""Domains anyone can have an address at, so all of one is no one's type. GitHub's private commit
addresses are one person each, but everyone with one shares the domain."""


def contact_type(text: str) -> str:
    """`text` as a contact type; raises ValueError for one that can't be."""
    found = text.strip().lower()
    if not _CONTACT_TYPE.fullmatch(found):
        raise ValueError(
            f"{text!r} isn't a contact type: start with a letter, then up to 39 letters, digits, "
            "spaces, - or _"
        )
    return found


def contact_key(text: str) -> str:
    """`text` as what a type is kept on, an address or `@domain`, lower-cased; raises ValueError
    for anything else, a shared mailbox, or a domain anyone can have an address at."""
    found = text.strip().lower()
    if _DOMAIN.fullmatch(found):
        if found[1:] in PUBLIC_DOMAINS:
            raise ValueError(f"anyone can have an address at {found[1:]}: give the whole address")
        return found
    if email := personal_email(found):
        return email
    raise ValueError(f"{text!r} isn't one person's email address or an @domain")


def clear_label(text: str, clear: Callable[[str], bool]) -> tuple[str, bool]:
    """Clear the label `text` names with `clear` (which says whether one was kept), lower-cased,
    so one kept on what can't be labelled any more (a domain that's since become public) still
    goes. The label, and whether one was kept; for text that names nothing kept, raises
    ValueError as `contact_key` does when it couldn't be a label either."""
    found = text.strip().lower()
    if clear(found):
        return found, True
    contact_key(text)
    return found, False


class PeopleConfig(Strict):
    """`[people]`: what the types of people I deal with mean."""

    contact_types: dict[str, Annotated[int, Field(ge=0, le=2)]] = Field(
        default_factory=lambda: dict(DEFAULT_CONTACT_TYPES)
    )
    """The types offered, each with the importance it gives items about such a person: 0 normal,
    1 high, 2 critical. Another type can still be set; it counts as 0."""

    @field_validator("contact_types")
    @classmethod
    def _types(cls, value: dict[str, int]) -> dict[str, int]:
        return {contact_type(name): importance for name, importance in value.items()}


def _tool_type(text: str | None) -> str | None:
    """A tool's contact type as one of mine would be kept; None when it can't be one."""
    try:
        return contact_type(text) if text else None
    except ValueError:
        return None


class _PersonView(Model):
    """What core reads of a `person`."""

    id: str
    email: str | None = None
    contact_type: str | None = None
    importance: int | None = None


@dataclass(frozen=True)
class Contact:
    """A person, as far as I've said or a tool knows."""

    email: str | None
    contact_type: str | None
    importance: int
    labelled: str | None
    """What my label on them is kept on, an address or `@domain`; None without one of mine."""
    stated: bool = False
    """Their importance is what a tool said outright, above what their type gives."""


class Contacts:
    """Who the people items are about are: what ranks them, and what's shown on them."""

    def __init__(self, labels: dict[str, str], graph: Graph, config: PeopleConfig):
        self._labels = labels
        self._graph = graph
        self._types = config.contact_types

    def _contact(self, person: _PersonView) -> Contact:
        email = personal_email(person.email)
        keys = [email, f"@{email.split('@')[1]}"] if email else []
        labelled = next((key for key in keys if key in self._labels), None)
        if labelled is not None and not labelled.startswith("@"):
            # Mine wins: a type I set on their address is how much they matter, whatever a tool
            # says.
            found = self._labels[labelled]
            return Contact(email, found, self._types.get(found, 0), labelled)
        same = self._graph.view_same_person(person.id, _PersonView)
        if labelled is not None:
            found: str | None = self._labels[labelled]  # on their whole domain
        else:
            # Where tools disagree, the first in the graph's order: the plugins' order.
            found = next((t for p in same if (t := _tool_type(p.contact_type))), None)
        by_type = self._types.get(found, 0) if found else 0
        # A tool can also state it outright (a CRM's VIP, a starred contact): the highest counts,
        # above a type on their domain too (a VIP at a customer).
        stated = max((p.importance or 0 for p in same), default=0)
        return Contact(email, found, max(by_type, stated), labelled, stated > by_type)

    def of(self, ref: Ref) -> Contact | None:
        """Who `ref` is about, itself when it's a person: the one who matters most, the first of
        those alike; None when it's about no one."""
        people = (
            [person]
            if ref.kind == PERSON and (person := self._graph.view_one(PERSON, ref.id, _PersonView))
            else self._graph.view_related(ref, PERSON, _PersonView)
        )
        contacts = [self._contact(person) for person in people]
        return max(contacts, key=lambda c: c.importance, default=None)

    def importance(self, ref: Ref) -> int:
        """The importance who `ref` is about gives it: by their type, or as a tool says."""
        contact = self.of(ref)
        return contact.importance if contact else 0

    def additions(self, item: Item) -> ItemAdditions:
        """A badge with the type of who `item` is mainly about (and "VIP" when a tool says they
        matter more than that), and a button to set it."""
        contact = self.of(item.subjects[0]) if item.subjects else None
        if contact is None:
            return ItemAdditions()
        return ItemAdditions(
            badges=[
                *([contact.contact_type] if contact.contact_type else []),
                *(["VIP"] if contact.stated else []),
            ],
            actions=self._actions(contact),
        )

    def _actions(self, contact: Contact) -> list[Action]:
        if contact.email is None:
            return []  # nothing to keep a type on
        domain = contact.email.split("@")[1]
        scopes = {contact.email: contact.email}
        if domain not in PUBLIC_DOMAINS:
            scopes[f"everyone at {domain}"] = f"@{domain}"
        return [
            Action(
                name=CONTACT_ACTION,
                label="Set type",
                fields={},
                input=next(iter(self._types), "customer"),
                suggestions=list(self._types),
                choices=scopes,
                group="Contact",
            ),
            *(
                [
                    Action(
                        name=CLEAR_CONTACT_ACTION,
                        label=f"Clear type for everyone at {contact.labelled[1:]}",
                        fields={"who": contact.labelled},
                        group="Contact",
                        confirm=f"Clear the type of everyone at {contact.labelled[1:]}?",
                    )
                    if contact.labelled.startswith("@")
                    else Action(
                        name=CLEAR_CONTACT_ACTION,
                        label="Clear type",
                        fields={"who": contact.labelled},
                        group="Contact",
                        confirm=f"Clear the type of {contact.labelled}?",
                    )
                ]
                if contact.labelled
                else []
            ),
        ]
