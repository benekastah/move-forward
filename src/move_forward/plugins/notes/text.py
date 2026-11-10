"""What the notes read in the text I write them: a todo's size, the PRs a line names, and the
PR a merge condition holds."""

import re

from ...api.kinds import PULL_REQUEST as PR
from .models import Condition, Link, Size

PR_URL_RE = re.compile(r"https://[\w.-]+(?::\d+)?/[\w.-]+/[\w.-]+/pull/\d+")
"""A pull request's URL, on github.com or a GitHub Enterprise Server."""
_SIZE_RE = re.compile(r"^\[(quick|big)\]\s*", re.IGNORECASE)


def links_in(text: str) -> list[Link]:
    """The PRs `text` names by URL, each once. The tickets it names aren't links: the graph
    finds them (see `Graph.named`)."""
    return [Link(kind=PR, ref=url) for url in dict.fromkeys(PR_URL_RE.findall(text))]


CONDITION_LINE = "- Merge condition: "
"""How a condition that holds no PR is kept: as a line of its entry, as written."""


def attach_conditions(texts: list[str], entry_prs: list[str]) -> tuple[list[Condition], list[str]]:
    """Conditions written for a whole entry (as the notes kept them before each named its PR),
    each given its PR: the one its text names (dropping a leading `<url>: `); else each of the
    entry's PRs, which keeps them all held as before. With no PR at all, it can't hold anything
    yet, so it's kept as a line of the entry instead (`CONDITION_LINE`), to add again with its
    PR."""
    conditions: list[Condition] = []
    kept: list[str] = []
    for text in texts:
        named = list(dict.fromkeys(PR_URL_RE.findall(text)))
        if len(named) == 1 and text.startswith(f"{named[0]}: "):
            text = text.removeprefix(f"{named[0]}: ")
        if prs := named or entry_prs:
            conditions += [Condition(pr=pr, text=text) for pr in prs]
        else:
            kept.append(CONDITION_LINE + text)
    return conditions, kept


def sized(text: str) -> tuple[str, Size | None]:
    """A todo's text without its leading `[quick]` or `[big]`, and that size."""
    if match := _SIZE_RE.match(text):
        return text[match.end() :], "quick" if match.group(1).lower() == "quick" else "big"
    return text, None
