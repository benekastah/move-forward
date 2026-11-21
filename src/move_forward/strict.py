"""The base of config models, and the markers their fields can carry in `Annotated`."""

import sys
from typing import Any, TypeAliasType, get_args, get_origin

from pydantic import BaseModel, ConfigDict, RootModel
from pydantic.fields import FieldInfo


class Locked:
    """The type of `LOCKED`."""

    def __repr__(self) -> str:
        return "LOCKED"


LOCKED = Locked()
"""Marks a config field as one the settings page shows but won't change, as it says where a
credential goes (a site, a host), where items link with my login in the link, whose word
decides a sender is who they claim, or what runs where: `site: Annotated[str, LOCKED]`. The
page is reached with the dashboard's cookie, and one that could point the site at another
server could send it the token; only the config file changes such a setting."""
Marker = Locked
"""A marker a config field can carry, each a type of its own (a union, once there are more), so
pydantic keeps every one on a field carrying several."""


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
    plugin's config model again (`check_resolved_markers`), once a field naming a type defined
    later has been resolved."""
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


def check_resolved_markers(model: type[BaseModel]) -> None:
    """`check_markers` once `model`'s fields naming a type defined after it are resolved: it's
    rebuilt against its own module's names, so no name of the caller's can stand in for one of
    them."""
    _resolve(model)
    check_markers(model)


def _resolve(model: type[BaseModel]) -> None:
    """Rebuild `model` against its own module's names, resolving its fields naming a type
    defined after it."""
    module = sys.modules.get(model.__module__)
    model.model_rebuild(_types_namespace=vars(module) if module else None)


def _model(annotation: object) -> type[BaseModel] | None:
    """`annotation`, if it's a model class."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def loose_models(model: type[BaseModel]) -> list[str]:
    """The names of the models, `model` and any a field's type holds (`dict[str, Account]`,
    `Account | None`, a `type` alias's value), that ignore unknown keys rather than reject
    them, as a `Strict` does: a typo there would be dropped without a word. Each held model's
    late types are resolved before it's looked into; a `RootModel`, which has no keys, is
    looked into but never loose. Run on a model whose late types are resolved
    (`check_resolved_markers`)."""
    loose: list[str] = []
    seen: set[int] = set()

    def visit(annotation: object) -> None:
        if id(annotation) in seen:
            return
        seen.add(id(annotation))
        if (held := _model(annotation)) is not None:
            _resolve(held)  # its own late types, which `model`'s rebuild leaves alone
            # A RootModel has no keys to misspell, and can't set `extra`.
            if not issubclass(held, RootModel) and held.model_config.get("extra") != "forbid":
                loose.append(held.__name__)
            for field in held.model_fields.values():
                visit(field.annotation)
            return
        inner: tuple[object, ...] = get_args(annotation)
        for alias in (annotation, get_origin(annotation)):
            if isinstance(alias, TypeAliasType):
                inner = (*inner, alias.__value__)
        for arg in inner:
            visit(arg)

    visit(model)
    return loose


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
    """Whether `field` carries `marker` (`LOCKED`) in its `Annotated` metadata."""
    return any(isinstance(m, type(marker)) for m in field.metadata)
