"""`[vcard]`."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator

from ...api import Strict

DEFAULT_REFRESH_SECONDS = 3600


class VcardConfig(Strict):
    """`[vcard]`: an address book, as vCard files. Optional.

    Its people are published with how much they matter and what they are to me, by the groups
    they're in, so core ranks the items about them (their mail, their PRs) by it.
    """

    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, ge=60)
    """How often the dashboard reads the files anyway; it also looks every minute for a changed
    file, and reads them at once if there is one."""
    paths: list[Path] = Field(min_length=1)
    """vCard files (`.vcf`, one or many contacts each), or directories searched for them, e.g.
    an export, or the directory vdirsyncer keeps in sync with a CardDAV server (iCloud,
    Fastmail, Nextcloud)."""
    group_importance: dict[str, Annotated[int, Field(ge=0, le=2)]] = Field(
        default_factory=dict[str, int]
    )
    """How much the people in a group matter to me (0 normal, 1 high, 2 critical), by the
    group's name, ignoring case, e.g. `{Family = 2, Favorites = 1}`: the highest of their
    groups' counts. A group is a card's category (`CATEGORIES`) or a group card naming them."""
    group_types: dict[str, str] = Field(default_factory=dict[str, str])
    """What the people in a group are to me, a contact type (see `[people] contact_types`), by
    the group's name, ignoring case, e.g. `{Clients = "customer"}`: the first listed of their
    groups'. A type I set on someone beats it."""

    @field_validator("paths")
    @classmethod
    def _expand(cls, value: list[Path]) -> list[Path]:
        return [p.expanduser() for p in value]
