"""`mfd init`'s questions for `[vcard]`."""

from pathlib import Path

from ...api import Asker, InitSetup, directories

TEMPLATE = """\
# refresh_seconds = 3600                # how often it reads the files anyway; it checks for changed ones every minute
# group_importance = {}                 # how much a group's people matter (0-2), e.g. {Family = 2, Favorites = 1}
# group_types = {}                      # what a group's people are to me, e.g. {Clients = "customer"}
"""
EXAMPLE = (
    '# paths = ["~/.contacts"]              # vCard files, or directories of them (vdirsyncer\'s)\n'
)
GUESSES = ("~/.contacts", "~/.local/share/contacts", "~/.local/share/vdirsyncer/contacts")
"""Where vdirsyncer's examples, and people following them, keep an address book."""


def _exists(answer: str) -> str:
    answer = answer.strip()
    if not answer:
        raise ValueError("a path is required")
    if not Path(answer).expanduser().exists():
        raise ValueError(f"{answer} isn't there")
    return answer


def questions(ask: Asker, setup: InitSetup) -> dict[str, object]:
    """Where the vCard files are, guessing vdirsyncer's usual directories."""
    del setup  # nothing in it says where an address book is
    guess = next((g for g in GUESSES if Path(g).expanduser().is_dir()), None)
    paths = ask.list(
        "vCard files, or directories of them, comma-separated (an export, or what vdirsyncer"
        " syncs from CardDAV)",
        [guess] if guess else [],
        _exists,
        directories,
    )
    return {"paths": paths}
