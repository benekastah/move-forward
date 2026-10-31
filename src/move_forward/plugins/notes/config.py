"""`[notes]`."""

from typing import cast

from pydantic import model_validator

from ...strict import Strict

IMPORT_ONCE = "the notes live in the database now: import them once with `mfd notes import <dir>`"
"""What to say to a config that still names the markdown notes' directory."""


class NotesConfig(Strict):
    """`[notes]`: the work notes and what's on ice. Always on; the section is optional and has
    no settings yet."""

    @model_validator(mode="before")
    @classmethod
    def _no_dir(cls, data: object) -> object:
        if isinstance(data, dict) and "dir" in cast(dict[str, object], data):
            raise ValueError(f"`dir` is gone; {IMPORT_ONCE}")
        return cast(object, data)
