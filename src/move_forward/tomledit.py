"""Changing a key of a TOML file as text, keeping everything else as written: comments,
order, blank lines, hand edits.

The standard library reads TOML (`tomllib`) but doesn't write it, and a library that does
would be a new dependency (see AGENTS.md). The config file only ever needs a key's line
changed, which this does: the key's own line if it's set, else its commented-out default
(as `mfd init` writes them), uncommented, else a new line at the end of its section.
Callers parse the result again to check it says what they meant.
"""

import json
import re
from pathlib import Path
from typing import cast

_HEADER_RE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(?:#.*)?$")
_COMMENTED_HEADER_RE = re.compile(r"^\s*#\s*\[")
"""A section `mfd init` wrote commented out, e.g. `# [jira]` for a plugin left out."""


def value(new: object) -> str:
    """`new` as a TOML value: a string, number, boolean, list, or table (written inline)."""
    if isinstance(new, dict):
        table = cast(dict[object, object], new)
        items = ", ".join(f"{key} = {value(v)}" for key, v in table.items())
        return "{ " + items + " }" if items else "{}"
    if isinstance(new, (list, tuple)):
        return "[" + ", ".join(value(v) for v in cast(list[object], new)) + "]"
    if isinstance(new, Path):
        new = str(new)
    return json.dumps(new)  # JSON's strings, numbers, true and false are TOML's too


def _comment_start(text: str) -> int:
    """Where a `#` comment starts in `text`, outside strings; its length when there's none."""
    quote: str | None = None
    escaped = False
    for i, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return i
    return len(text)


def _inline_comment(rest: str) -> str:
    """The comment after a value (`  # how often`), with the space before it, or ""."""
    start = _comment_start(rest)
    if start == len(rest):
        return ""
    gap = len(rest[:start]) - len(rest[:start].rstrip())
    return rest[start - gap :]


def set_key(text: str, section: str | None, key: str, new: object) -> str:
    """`text` with `key` in `[section]` (None: the top level, before any section) set to
    `new`, everything else as it was."""
    lines = text.split("\n")
    where: list[str | None] = []
    current: str | None = None
    for line in lines:
        if header := _HEADER_RE.match(line):
            current = header.group(1)
        where.append(current)
    in_section = [i for i, s in enumerate(where) if s == section]
    set_re = re.compile(rf"^(\s*){re.escape(key)}\s*=(.*)$")
    default_re = re.compile(rf"^(\s*)#\s*{re.escape(key)}\s*=(.*)$")
    line = f"{key} = {value(new)}"
    for i in in_section:
        if match := set_re.match(lines[i]):
            lines[i] = f"{match.group(1)}{line}{_inline_comment(match.group(2))}"
            return "\n".join(lines)
    for i in in_section:
        if _COMMENTED_HEADER_RE.match(lines[i]):  # another section's commented-out defaults
            break
        if match := default_re.match(lines[i]):
            lines[i] = f"{match.group(1)}{line}{_inline_comment(match.group(2))}"
            return "\n".join(lines)
    if section is None:
        first_header = next((i for i, s in enumerate(where) if s is not None), len(lines))
        last = max(
            (
                i
                for i in range(first_header)
                if lines[i].strip() and not lines[i].lstrip().startswith("#")
            ),
            default=-1,
        )
        lines.insert(last + 1, line)
        return "\n".join(lines)
    if not in_section:
        ending = "" if text.endswith("\n") else "\n"
        return text + ending + f"\n[{section}]\n{line}\n"
    last = max(i for i in in_section if lines[i].strip())
    lines.insert(last + 1, line)
    return "\n".join(lines)
