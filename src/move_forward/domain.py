"""The base of every model: plugins' data, the page's, the graph's."""

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    """Base for every model: immutable, and unknown keys are dropped rather than rejected."""

    model_config = ConfigDict(extra="ignore", frozen=True)
