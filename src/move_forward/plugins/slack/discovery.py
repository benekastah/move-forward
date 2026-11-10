"""Match GitHub team slugs to Slack channel metadata, without inventing a destination."""

import re

from .client import Channel


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def candidates(team: str, channels: list[Channel]) -> list[dict[str, str]]:
    """Exact team/channel names or whole phrases in topics and purposes are candidates.
    Multiple matches remain ambiguous: a conventional channel name isn't proof of intent."""
    words = _words(team)
    found: list[dict[str, str]] = []
    for channel in sorted(channels, key=lambda c: (c.name, c.id)):
        matched: list[str] = []
        if _words(channel.name) in (words, f"team {words}"):
            matched.append("name")
        for field, value in (("topic", channel.topic), ("purpose", channel.purpose)):
            if f" {words} " in f" {_words(value)} ":
                matched.append(field)
        if matched:
            found.append(
                {
                    "channel_name": f"#{channel.name}",
                    "channel_id": channel.id,
                    "topic": channel.topic,
                    "purpose": channel.purpose,
                    "matched_on": ", ".join(matched),
                }
            )
    return found
