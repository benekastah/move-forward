"""The base of config models, and the markers their fields can carry."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

LOCKED = "locked"
"""The marker of a field the settings page shows but won't change (`locked`)."""
TABLES = "tables"
"""The marker of a field whose entries are tables of their own, by name (mail's
`[mail.<name>]` accounts): `mfd init` leaves it to the plugin's example tables."""


class Strict(BaseModel):
    """Config models reject unknown keys, so a typo in the file is an error, not silence."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, use_attribute_docstrings=True
    )
    """Attribute docstrings become fields' descriptions: the settings page's help."""


def locked() -> Any:
    """Marks a config field as one the settings page shows but won't change, as it says where a
    credential goes (a site, a host), where items link with my login in the link, whose word
    decides a sender is who they claim, or what runs where: `site: Annotated[str, locked()]`.
    The page is reached with the dashboard's cookie, and one that could point the site at
    another server could send it the token; only the config file changes such a setting."""
    return Field(json_schema_extra={LOCKED: True})


def marked(field: FieldInfo, marker: str) -> bool:
    """Whether `field` carries `marker` (`LOCKED`, `TABLES`) in its `json_schema_extra`,
    whatever else that holds."""
    extra = field.json_schema_extra
    return extra is not None and not callable(extra) and extra.get(marker) is True
