"""Reading an address book's vCard files (RFC 6350, and the 3.0 and 2.1 still exported), as far
as the plugin needs: names, addresses and groups. Nothing is written."""

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
    emails: list[str] = field(default_factory=list[str])
    categories: list[str] = field(default_factory=list[str])
    group: bool = False
    """It's a group's card (`KIND:group`), naming its `members` by their `UID`."""
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
    groups: dict[str, list[str]] = {}
    for card in cards:
        if card.group and card.name:
            for member in card.members:
                groups.setdefault(_uid(member), []).append(card.name)
    names: dict[str, str] = {}
    their_groups: dict[str, list[str]] = {}
    for card in cards:
        if card.group:
            continue
        named = [*card.categories, *(groups.get(_uid(card.uid), []) if card.uid else [])]
        for email in card.emails:
            names.setdefault(email, card.name or email)
            their_groups.setdefault(email, []).extend(named)
    return VcardSnapshot(
        contacts=[
            Contact(email=email, name=name, groups=list(dict.fromkeys(their_groups[email])))
            for email, name in names.items()
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
        return path.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError as e:
        raise SourceError(f"can't read {path}: {e}") from e


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
    for line in _lines(text):
        name, params, value = _property(line)
        if name == "BEGIN" and value.upper() == "VCARD":
            card = Card()
        elif card is None:
            continue
        elif name == "END" and value.upper() == "VCARD":
            cards.append(card)
            card = None
        else:
            _read_property(card, name, _decoded(params, value))
    return cards


def _read_property(card: Card, name: str, value: str) -> None:
    if name == "FN":
        card.name = _unescape(value).strip() or card.name
    elif name == "N" and not card.name:
        family, given, *more = [*_split(value, ";"), "", ""]
        additional, prefix, suffix = [*more, "", "", ""][:3]
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
    last) joined, as are 2.1's quoted-printable values broken with a trailing `=`."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
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
            parts.append(_unescape("".join(current)))
            current = []
        else:
            current.append(c)
    return [*parts, _unescape("".join(current))]


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
