"""The base of config models, and the markers their fields can carry in `Annotated`."""

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo


class Locked:
    """The type of `LOCKED`."""

    def __repr__(self) -> str:
        return "LOCKED"


class Tables:
    """The type of `TABLES`."""

    def __repr__(self) -> str:
        return "TABLES"


LOCKED = Locked()
"""Marks a config field as one the settings page shows but won't change, as it says where a
credential goes (a site, a host), where items link with my login in the link, whose word
decides a sender is who they claim, or what runs where: `site: Annotated[str, LOCKED]`. The
page is reached with the dashboard's cookie, and one that could point the site at another
server could send it the token; only the config file changes such a setting."""
TABLES = Tables()
"""Marks a config field whose entries are tables of their own, by name (mail's
`[mail.<name>]` accounts): `accounts: Annotated[dict[str, Account], TABLES] = ...`. `mfd init`
leaves it to the plugin's example tables."""
Marker = Locked | Tables
"""A marker a config field can carry. Each is a type of its own, so pydantic keeps both on a
field carrying both."""


class Strict(BaseModel):
    """Config models reject unknown keys, so a typo in the file is an error, not silence."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, use_attribute_docstrings=True
    )
    """Attribute docstrings become fields' descriptions: the settings page's help."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a marker given as a field's default (`host: str = LOCKED`), which would mark
        nothing: a locked field left editable, without a word."""
        super().__pydantic_init_subclass__(**kwargs)
        if misplaced := [k for k, f in cls.model_fields.items() if isinstance(f.default, Marker)]:
            raise TypeError(
                f"{cls.__name__}: {', '.join(misplaced)} has a marker as its default; mark it"
                " in its type, e.g. `host: Annotated[str, LOCKED]`"
            )


def marked(field: FieldInfo, marker: Marker) -> bool:
    """Whether `field` carries `marker` (`LOCKED`, `TABLES`) in its `Annotated` metadata."""
    return any(isinstance(m, type(marker)) for m in field.metadata)
