"""The base of every model: plugins' data, the page's, the graph's."""

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    """Base for every model: immutable, and unknown keys are dropped rather than rejected."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class KindModel(Model):
    """Base for an entity kind's attributes (`KindSpec.attrs`): unknown keys are rejected, so
    a publisher's misspelt attribute fails the graph's check instead of quietly publishing
    nothing. A consumer's view of a kind stays a plain `Model`, reading what it needs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
