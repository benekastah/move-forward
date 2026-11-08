"""The vcard plugin: an address book's people, by its groups, so the ones who matter rank first."""

from typing import ClassVar

from ...api import (
    API_VERSION,
    Asker,
    Changed,
    ChangedRequest,
    CollectRequest,
    EntitiesRequest,
    Entity,
    InitSetup,
    KindSpec,
    LoadRequest,
    Migration,
    Plugin,
    Ref,
    StoreRequest,
    attrs,
)
from ...api.kinds import PERSON
from . import client
from .config import VcardConfig
from .kinds import VcardPerson
from .models import Contact, VcardSnapshot
from .repository import MIGRATIONS, ContactRepository
from .setup import EXAMPLE, TEMPLATE, questions

SIGNATURE = "signature"
"""The kv key of the files' `client.signature` at the last look, for `changed`."""


def person_id(email: str) -> str:
    """The id of the `person` with address `email`: `vcard/<address>`."""
    return f"vcard/{email}"


def importance(config: VcardConfig, contact: Contact) -> int | None:
    """The highest importance of their groups in `group_importance`; None when it names none."""
    groups = {g.casefold() for g in contact.groups}
    found = [n for name, n in config.group_importance.items() if name.casefold() in groups]
    return max(found, default=None)


def contact_type(config: VcardConfig, contact: Contact) -> str | None:
    """The type of the first group in `group_types` they're in; None when they're in none."""
    groups = {g.casefold() for g in contact.groups}
    return next((t for name, t in config.group_types.items() if name.casefold() in groups), None)


class VcardPlugin(Plugin[VcardConfig, VcardSnapshot]):
    name = "vcard"
    title = "vCard"
    init_template = TEMPLATE
    init_example = EXAMPLE
    config_model = VcardConfig
    api_version = API_VERSION
    snapshot_model = VcardSnapshot
    migrations: ClassVar[list[Migration]] = MIGRATIONS
    kinds: ClassVar[dict[str, KindSpec]] = {PERSON: KindSpec(VcardPerson)}
    change_check_seconds: ClassVar[int | None] = 60

    def init_suggested(self) -> bool:
        """Only when asked for: it needs an address book's files."""
        return False

    def init_questions(self, ask: Asker, setup: InitSetup) -> dict[str, object]:
        return questions(ask, setup)

    def collect(self, req: CollectRequest[VcardConfig]) -> VcardSnapshot:
        return client.collect(req.ctx.config)

    def store(self, req: StoreRequest[VcardConfig, VcardSnapshot]) -> None:
        ContactRepository(req.ctx.db).replace(req.snapshot)

    def load(self, req: LoadRequest[VcardConfig]) -> VcardSnapshot | None:
        return ContactRepository(req.ctx.db).load()

    def changed(self, req: ChangedRequest[VcardConfig]) -> Changed:
        """Whether a file was added, removed or written since the last look, by their times and
        sizes, without reading them."""
        ctx = req.ctx
        now = client.signature(ctx.config)
        before = ctx.kv.get(SIGNATURE)
        ctx.kv.set(SIGNATURE, now)
        return Changed(before is not None and before != now, "the address book changed")

    def entities(self, req: EntitiesRequest[VcardConfig, VcardSnapshot]) -> list[Entity]:
        config, snapshot = req.ctx.config, req.snapshot
        if snapshot is None:
            return []
        return [
            Entity(
                ref=Ref(kind=PERSON, id=person_id(contact.email)),
                title=contact.name,
                attrs=attrs(
                    VcardPerson(
                        handle=contact.email,
                        email=contact.email,
                        contact_type=contact_type(config, contact),
                        importance=importance(config, contact),
                        groups=contact.groups,
                    )
                ),
            )
            for contact in snapshot.contacts
        ]
