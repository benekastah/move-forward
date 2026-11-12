"""The base of config models, and the markers their fields can carry in `Annotated`."""

from typing import Any, TypeAliasType, get_args, get_origin

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
        """Refuse a marker that would mark nothing (`check_markers`), as the class is defined."""
        super().__pydantic_init_subclass__(**kwargs)
        check_markers(cls)


def check_markers(model: type[BaseModel]) -> None:
    """Raise TypeError for a marker in `model` that would mark nothing, leaving a locked field
    editable without a word: one given as a field's default (`host: str = LOCKED`), or one
    inside its type rather than on it (`Annotated[str, LOCKED] | None`,
    `list[Annotated[str, LOCKED]]`, a `type` alias, generic or not), where pydantic doesn't put
    it in the field's metadata. `Strict` checks as a class is defined, and the registry each
    plugin's config model again, once a field naming a type defined later has been resolved."""
    fields = model.model_fields
    if misplaced := [k for k, f in fields.items() if isinstance(f.default, Marker)]:
        raise TypeError(
            f"{model.__name__}: {', '.join(misplaced)} has a marker as its default; mark it"
            " in its type, e.g. `host: Annotated[str, LOCKED]`"
        )
    if buried := [k for k, f in fields.items() if _buried(f)]:
        raise TypeError(
            f"{model.__name__}: {', '.join(buried)} has a marker inside its type, where it"
            " marks nothing; put it on the field's own type, e.g. `host: Annotated[str |"
            " None, LOCKED]`"
        )


def _markers_in(annotation: object, seen: set[int]) -> list[Marker]:
    """The markers anywhere in `annotation`: its arguments' (an `Annotated`'s metadata among
    them), and a `type` alias's value's, generic (`Locked[str]`) or not, each looked into
    once."""
    if id(annotation) in seen:
        return []
    seen.add(id(annotation))
    if isinstance(annotation, Marker):
        return [annotation]
    inner: tuple[object, ...] = get_args(annotation)
    for alias in (annotation, get_origin(annotation)):
        if isinstance(alias, TypeAliasType):
            inner = (*inner, alias.__value__)
    return [m for arg in inner for m in _markers_in(arg, seen)]


def _buried(field: FieldInfo) -> bool:
    """Whether a marker is in `field`'s type but not on the field (its metadata)."""
    return any(not marked(field, m) for m in _markers_in(field.annotation, set()))


def marked(field: FieldInfo, marker: Marker) -> bool:
    """Whether `field` carries `marker` (`LOCKED`, `TABLES`) in its `Annotated` metadata."""
    return any(isinstance(m, type(marker)) for m in field.metadata)
