"""`[notes]`."""

from pathlib import Path

from pydantic import field_validator

from ...strict import Strict

DEFAULT_NOTES_DIR = Path("~/.config/move-forward/notes")


class NotesConfig(Strict):
    """`[notes]`: the move-forward skill's notes. Always on; the section is optional."""

    dir: Path = DEFAULT_NOTES_DIR
    """The directory of work-notes.md, on-ice.md and reviewer-map.md, which the dashboard
    reads on every refresh and writes todos and icing to."""

    @field_validator("dir")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()
