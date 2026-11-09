"""Text as shown: what a stranger wrote (a subject, a name, a PR's title), without what could
make it read as something else."""

import re
import unicodedata

_HIDDEN = {
    chr(c)
    for c in (
        0x00AD,  # soft hyphen
        0x061C,
        0x115F,  # Hangul fillers, which show as nothing
        0x1160,
        0x200B,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        0x2060,  # word joiner
        *range(0x2066, 0x206A),
        0x3164,
        0xFEFF,  # zero-width no-break space
        0xFFA0,
    )
}
"""Characters that change how text reads, or hide it, without showing: bidi controls, the
zero-width space and no-break space, the word joiner, the soft hyphen and Hangul fillers. Kept:
ZWJ and ZWNJ, which emoji and scripts (Persian, Indic) need."""

_CODE = r"[\U000E0030-\U000E0039\U000E0061-\U000E007A]{2,6}"  # tag digits and lower-case letters
_TAGS = re.compile(rf"(\U0001F3F4{_CODE}\U000E007F)|[\U000E0000-\U000E007F]")
"""A flag made of tag characters, kept whole: a black flag, a region's code in two to six tag
letters or digits (England's is "gbeng") and a cancel tag. Or a tag character outside one,
which shows nothing and could carry a hidden message."""


def shown(text: str) -> str:
    """`text` without the characters in `_HIDDEN`, which could make text a stranger wrote read
    as something else, e.g. a right-to-left override turning `fdp.exe` into `exe.pdf`; control
    characters as spaces; tag characters but a flag's; and its whitespace collapsed. A hidden
    character goes without a trace, as it may sit inside a word (a soft hyphen). Core shows
    every item's title, subtitle, details, badges and links' text through it, and every
    notification's."""
    text = _TAGS.sub(lambda m: m[1] or "", text)
    kept = "".join(" " if unicodedata.category(c) == "Cc" else c for c in text if c not in _HIDDEN)
    return " ".join(kept.split())
