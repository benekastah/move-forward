"""The base of config models."""

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    """Config models reject unknown keys, so a typo in the file is an error, not silence."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, use_attribute_docstrings=True
    )
    """Attribute docstrings become fields' descriptions: the settings page's help."""
