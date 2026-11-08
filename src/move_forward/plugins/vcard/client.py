"""Reading an address book's vCard files (RFC 6350, and the 3.0 and 2.1 still exported), as far
as the plugin needs: names, addresses and groups. Nothing is written."""

import codecs
import hashlib
import quopri
from dataclasses import dataclass, field
from pathlib import Path

from ...api import SourceError
from .config import VcardConfig
from .models import Contact, VcardSnapshot

SUFFIX = ".vcf"
_GROUP_KINDS = ("KIND", "X-ADDRESSBOOKSERVER-KIND")
"""Where a card says it's a group: vCard 4's `KIND`, and Apple's (iCloud's CardDAV)."""
_MEMBERS = ("MEMBER", "X-ADDRESSBOOKSERVER-MEMBER")
_UUID_URN = "urn:uuid:"


@dataclass
class Card:
    """One vCard, as far as it's read."""

    uid: str | None = None
    name: str = ""
    """Its first `FN`, else its `N`."""
    has_fn: bool = False
    """`name` is its first `FN`, which later ones and its `N` don't replace."""
    emails: list[str] = field(default_factory=list[str])
    categories: list[str] = field(default_factory=list[str])
    group: bool = False
    """It's a group's card (`KIND:group`), naming its `members` by their `UID` or address
    (`mailto:`)."""
    members: list[str] = field(default_factory=list[str])


def files(config: VcardConfig) -> list[Path]:
    """The vCard files the config names, in order: each file, and each directory's `.vcf`
    files below it, by path. Raises SourceError for a path that isn't there."""
    found: list[Path] = []
    for path in config.paths:
        if path.is_dir():
            found += sorted(
                p for p in path.rglob("*") if p.suffix.lower() == SUFFIX and p.is_file()
            )
        elif path.is_file():
            found.append(path)
        else:
            raise SourceError(f"{path} isn't there")
    return found


def collect(config: VcardConfig) -> VcardSnapshot:
    """Everyone with an address in the config's files: one `Contact` per address, the first
    card's name for it, and the groups of every card giving it."""
    cards = [card for path in files(config) for card in parse(_read(path))]
    by_uid: dict[str, list[str]] = {}
    by_email: dict[str, list[str]] = {}
    for card in cards:
        if card.group and card.name:
            for member in card.members:
                email = _email(member) if member.lower().startswith("mailto:") else None
                if email:
                    by_email.setdefault(email, []).append(card.name)
                else:
                    by_uid.setdefault(_uid(member), []).append(card.name)
    names: dict[str, str] = {}
    their_groups: dict[str, list[str]] = {}
    for card in cards:
        if card.group:
            continue
        named = [*card.categories, *(by_uid.get(_uid(card.uid), []) if card.uid else [])]
        for email in card.emails:
            if card.name:
                names.setdefault(email, card.name)
            their_groups.setdefault(email, []).extend([*named, *by_email.get(email, [])])
    return VcardSnapshot(
        contacts=[
            Contact(email=email, name=names.get(email, email), groups=list(dict.fromkeys(groups)))
            for email, groups in their_groups.items()
        ]
    )


def signature(config: VcardConfig) -> str:
    """What changes when a file is added, removed or written: each one's path, time and size."""
    seen = hashlib.sha256()
    for path in files(config):
        stat = path.stat()
        seen.update(f"{path.as_posix()}\0{stat.st_mtime_ns}\0{stat.st_size}\n".encode())
    return seen.hexdigest()


def _read(path: Path) -> str:
    try:
        return decode(path.read_bytes())
    except OSError as e:
        raise SourceError(f"can't read {path}: {e}") from e


def decode(raw: bytes) -> str:
    """A file's text: UTF-16 by its byte-order mark, else UTF-8, else (2.1's raw 8-bit
    values, as old exports write them) Windows-1252."""
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _uid(text: str) -> str:
    """A `UID` or a member's reference to one, as the two are compared: without `urn:uuid:`,
    ignoring case."""
    text = text.strip()
    if text.lower().startswith(_UUID_URN):
        text = text[len(_UUID_URN) :]
    return text.lower()


def parse(text: str) -> list[Card]:
    """The cards in a vCard file's text."""
    cards: list[Card] = []
    card: Card | None = None
    nested = 0  # 2.1's `AGENT` can hold a whole card, which isn't this one's
    for line in _lines(text):
        name, params, value = _property(line)
        begins = name == "BEGIN" and value.upper() == "VCARD"
        ends = name == "END" and value.upper() == "VCARD"
        if card is None:
            card = Card() if begins else None
        elif begins or nested:
            nested += 1 if begins else -1 if ends else 0
        elif ends:
            cards.append(card)
            card = None
        else:
            _read_property(card, name, _decoded(params, value))
    return cards


def _read_property(card: Card, name: str, value: str) -> None:
    if name == "FN":
        fn = _unescape(value).strip()
        if fn and not card.has_fn:
            card.name, card.has_fn = fn, True
    elif name == "N" and not card.name:
        # Each part may be a list (`Jane,Ann`): its items are names too.
        parts = [" ".join(_split(p, ",")) for p in _split_raw(value, ";")]
        family, given, additional, prefix, suffix = [*parts, "", "", "", "", ""][:5]
        card.name = " ".join(p for p in (prefix, given, additional, family, suffix) if p.strip())
    elif name == "EMAIL":
        email = _email(_unescape(value))
        if email and email not in card.emails:
            card.emails.append(email)
    elif name == "CATEGORIES":
        card.categories += [c.strip() for c in _split(value, ",") if c.strip()]
    elif name == "UID":
        card.uid = _unescape(value).strip() or None
    elif name in _GROUP_KINDS:
        card.group = card.group or value.strip().lower() == "group"
    elif name in _MEMBERS:
        card.members.append(value.strip())


def _email(text: str) -> str | None:
    """An address as kept: lower-case, without `mailto:`; None for what can't be one (no `@`,
    or a space or `/`, which can't go in a person's id)."""
    email = text.strip().lower().removeprefix("mailto:")
    if email.count("@") != 1 or any(c.isspace() or c == "/" for c in email):
        return None
    return email


def _lines(text: str) -> list[str]:
    """The file's logical lines: folded ones (a line starting with a space or tab continues the
    last) joined, as are 2.1's quoted-printable values broken with a trailing `=`. 2.1 folds
    at whitespace and keeps it; later versions fold anywhere and drop the space or tab."""
    lines: list[str] = []
    old = False  # in a 2.1 card
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.upper().startswith("VERSION:"):
            old = raw[8:].strip() == "2.1"
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw if old else raw[1:]
        elif lines and lines[-1].endswith("=") and _quoted_printable(lines[-1]):
            lines[-1] = lines[-1][:-1] + raw
        elif raw:
            lines.append(raw)
    return lines


def _quoted_printable(line: str) -> bool:
    head = _unquoted_split(line, ":", 1)[0].upper()
    return "QUOTED-PRINTABLE" in head


def _property(line: str) -> tuple[str, dict[str, str], str]:
    """A line's property name (upper-case, without its `item1.` group), its parameters
    (upper-case names; a bare one, as 2.1 writes `TYPE`s, by itself) and its raw value."""
    head, *rest = _unquoted_split(line, ":", 1)
    value = rest[0] if rest else ""
    name, *raw_params = _unquoted_split(head, ";")
    params: dict[str, str] = {}
    for param in raw_params:
        key, sep, val = param.partition("=")
        params[key.strip().upper()] = val.strip().strip('"') if sep else key.strip().upper()
    return name.rpartition(".")[2].strip().upper(), params, value


def _unquoted_split(text: str, sep: str, limit: int = -1) -> list[str]:
    """`text` split at `sep` outside double quotes, at most `limit` times."""
    parts: list[str] = []
    start, quoted = 0, False
    for i, c in enumerate(text):
        if c == '"':
            quoted = not quoted
        elif c == sep and not quoted and (limit < 0 or len(parts) < limit):
            parts.append(text[start:i])
            start = i + 1
    return [*parts, text[start:]]


def _decoded(params: dict[str, str], value: str) -> str:
    """A 2.1 value written quoted-printable, decoded by its `CHARSET` (UTF-8 if none)."""
    if (
        params.get("ENCODING", "").upper() != "QUOTED-PRINTABLE"
        and "QUOTED-PRINTABLE" not in params
    ):
        return value
    raw = quopri.decodestring(value.encode("ascii", errors="replace"))
    try:
        return raw.decode(params.get("CHARSET", "utf-8"), errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _split(value: str, sep: str) -> list[str]:
    """A list or structured value split at `sep` but where it's escaped, each part unescaped."""
    return [_unescape(part) for part in _split_raw(value, sep)]


def _split_raw(value: str, sep: str) -> list[str]:
    """`value` split at `sep` but where it's escaped, its escapes kept."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for c in value:
        if escaped:
            current.append("\\" + c)
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == sep:
            parts.append("".join(current))
            current = []
        else:
            current.append(c)
    return [*parts, "".join(current)]


def _unescape(text: str) -> str:
    """A text value with vCard's escapes (`\\n`, `\\,`, `\\;`, `\\\\`) undone."""
    out: list[str] = []
    escaped = False
    for c in text:
        if escaped:
            out.append("\n" if c in "nN" else c)
            escaped = False
        elif c == "\\":
            escaped = True
        else:
            out.append(c)
    return "".join(out)
