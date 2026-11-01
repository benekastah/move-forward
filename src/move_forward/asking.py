"""Asking `mfd init`'s questions: an `Asker` that asks again until an answer passes its
check, the checks themselves, and the machine guesses' quiet commands. Core asks its own
questions with these, and each plugin its section's in `Plugin.init_questions`."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .shell import CommandError, run

Prompt = Callable[[str, str], str]
"""Asks a question with a default and returns the answer ("" means take the default)."""
Check = Callable[[str], str]
"""Normalises an answer, or raises ValueError saying what's wrong with it."""

MAX_RETRIES = 5


@dataclass
class InitSetup:
    """What `mfd init` knows so far, for the plugins' questions."""

    email: str
    cwd: Path
    """Where `mfd init` runs: its git checkout is a good guess for a repo."""
    teams: list[str] = field(default_factory=list[str])
    """The teams I'm on or review for. A plugin that can find them (github) sets them, and
    later plugins' questions use them (slack's channel per team)."""


class Asker:
    """Asks one question, again until `check` accepts the answer."""

    def __init__(self, prompt: Prompt):
        self._prompt = prompt

    def __call__(self, question: str, default: str, check: Check | None = None) -> str:
        """Ask until the answer passes `check`; a bad answer prints why and asks again."""
        for _ in range(MAX_RETRIES):
            answer = self._prompt(question, default) or default
            if check is None:
                return answer
            try:
                return check(answer)
            except ValueError as e:
                print(f"  {e}; try again")
        raise ValueError(f"no usable answer for: {question}")

    def list(self, question: str, default: list[str], check: Check | None = None) -> list[str]:
        """A comma-separated answer; each item goes through `check`."""

        def check_all(answer: str) -> str:
            parts = [part.strip() for part in answer.split(",") if part.strip()]
            return ", ".join(check(part) if check else part for part in parts)

        answer = self(question, ", ".join(default), check_all)
        return [part.strip() for part in answer.split(",") if part.strip()]


def quiet(args: list[str], cwd: Path | None = None) -> str:
    """A command's output, or "" when it fails or isn't installed: for guesses."""
    try:
        return run(args, cwd=cwd).strip()
    except (CommandError, FileNotFoundError):
        return ""


def word(what: str) -> Check:
    """A name like a GitHub org or team slug: letters, digits, `-`, `_`, `.`."""

    def check(answer: str) -> str:
        answer = answer.strip()
        if not answer:
            raise ValueError(f"{what} is required")
        if not re.fullmatch(r"[\w.-]+", answer):
            raise ValueError(f"{answer!r} isn't a {what}: use letters, digits, - and _")
        return answer

    return check


def url(answer: str) -> str:
    answer = answer.strip().rstrip("/")
    if answer and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", answer):
        raise ValueError(f"{answer!r} isn't a URL starting with https://")
    return answer


def https_url(answer: str) -> str:
    """A URL that must be `https://` (blank for none), e.g. one we send a token to."""
    answer = url(answer)
    if answer and not answer.startswith("https://"):
        raise ValueError(f"{answer!r} must start with https://")
    return answer


def email(answer: str) -> str:
    answer = answer.strip()
    if "@" not in answer or " " in answer:
        raise ValueError(f"{answer!r} isn't an email address")
    return answer


def channel(answer: str) -> str:
    answer = answer.strip()
    if answer and " " in answer:
        raise ValueError(f"{answer!r} isn't a channel name (e.g. #team-a)")
    return answer


def path(answer: str) -> str:
    answer = answer.strip()
    if not answer:
        raise ValueError("a path is required")
    if not Path(answer).expanduser().is_dir():
        raise ValueError(f"{answer} isn't a directory")
    return answer


def branch_prefix(answer: str) -> str:
    if " " in answer.strip():
        raise ValueError(f"{answer!r} has a space; a branch prefix is like jane/")
    return answer.strip()


def port(answer: str) -> str:
    if not answer.isdigit() or not 1 <= int(answer) <= 65535:
        raise ValueError(f"{answer!r} isn't a port number (1-65535)")
    return answer


def checkout(cwd: Path) -> Path | None:
    """The git checkout `cwd` is in, if any."""
    top = quiet(["git", "rev-parse", "--show-toplevel"], cwd)
    return Path(top) if top else None
