"""CODEOWNERS matching: which owners GitHub assigns to a path.

Follows GitHub's rules: the last matching line wins, patterns behave like .gitignore
(a leading or inner `/` anchors to the repo root, `*` stays within a path segment, `**`
crosses segments), and a pattern that names a directory owns everything below it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

CODEOWNERS_LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
_GLOB_CHARS = re.compile(r"(?<!\\)[*?\[]")


@dataclass(frozen=True)
class _Rule:
    line: int
    owners: tuple[str, ...]
    required: bool
    """The line carries the configured marker for owners whose approval is required."""


def _glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i)
            if end == -1:
                out.append(re.escape(char))
            else:
                out.append(pattern[i : end + 1])
                i = end + 1
                continue
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


class CodeOwners:
    """A parsed CODEOWNERS file."""

    def __init__(self, text: str, required_marker: str = ""):
        """`required_marker`, when set, is a comment token (e.g. `#required`) that marks a
        line's owners as required approvers."""
        self._literal: dict[str, _Rule] = {}
        """Anchored patterns without wildcards, keyed by path. Later lines overwrite."""
        self._globs: list[tuple[str, re.Pattern[str], _Rule]] = []
        """(literal prefix, regex, rule) for wildcard and unanchored patterns, in file order."""
        for line_no, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            pattern, comment = tokens[0], ""
            owners: list[str] = []
            for index, token in enumerate(tokens[1:], start=1):
                if token.startswith("#"):
                    comment = " ".join(tokens[index:])
                    break
                owners.append(token)
            required = bool(required_marker) and required_marker in comment
            rule = _Rule(line_no, tuple(owners), required)
            self._add(pattern, rule)

    def _add(self, pattern: str, rule: _Rule) -> None:
        anchored = "/" in pattern.rstrip("/")
        body = pattern.strip("/")
        if anchored and not _GLOB_CHARS.search(body):
            self._literal[body.replace("\\", "")] = rule
            return
        regex = _glob_to_regex(body)
        prefix_part = _GLOB_CHARS.split(body, maxsplit=1)[0].replace("\\", "")
        if anchored:
            compiled = re.compile(f"^{regex}(/.*)?$")
            prefix = prefix_part
        else:
            compiled = re.compile(f"^(.*/)?{regex}(/.*)?$")
            prefix = ""
        self._globs.append((prefix, compiled, rule))

    def _rule_for(self, path: str) -> _Rule | None:
        best: _Rule | None = None
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            rule = self._literal.get("/".join(parts[:end]))
            if rule and (best is None or rule.line > best.line):
                best = rule
        for prefix, regex, rule in self._globs:
            if (
                (best is None or rule.line > best.line)
                and path.startswith(prefix)
                and regex.match(path)
            ):
                best = rule
        return best

    def owners(self, path: str) -> tuple[tuple[str, ...], bool]:
        """(owners, required) for a repo-relative path; ((), False) if unowned."""
        rule = self._rule_for(path.lstrip("/"))
        return (rule.owners, rule.required) if rule else ((), False)


def load(repo_dir: Path, required_marker: str = "") -> CodeOwners | None:
    """The repo's CODEOWNERS file, from the places GitHub looks, or None."""
    for location in CODEOWNERS_LOCATIONS:
        path = repo_dir / location
        if path.exists():
            return CodeOwners(path.read_text(), required_marker)
    return None
