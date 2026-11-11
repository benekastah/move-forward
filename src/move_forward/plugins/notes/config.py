"""`[notes]`."""

from typing import cast

from pydantic import model_validator

from ...api import Strict

IN_THE_DATABASE = (
    "the notes live in the database, and this version reads no markdown: delete the line (to"
    " bring markdown notes in, make them `mfd notes` calls, or run `mfd import <dir>` with an"
    " earlier version first)"
)
"""What to say to a config that still names the markdown notes' directory."""


class NotesConfig(Strict):
    """`[notes]`: the work notes and what's on ice. Always on; the section is optional and has
    no settings yet."""

    @model_validator(mode="before")
    @classmethod
    def _no_dir(cls, data: object) -> object:
        if isinstance(data, dict) and "dir" in cast(dict[str, object], data):
            raise ValueError(f"`dir` is gone; {IN_THE_DATABASE}")
        return cast(object, data)
