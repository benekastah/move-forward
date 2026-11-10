"""`mfd init`'s questions for `[jira]`."""

from ...api import Asker, InitSection, InitSetup, https_url

EXAMPLE = InitSection({"site": "https://example.atlassian.net"})


def _required(answer: str) -> str:
    answer = https_url(answer)
    if not answer:
        raise ValueError("the site is required")
    return answer


def questions(ask: Asker, setup: InitSetup) -> InitSection:
    return InitSection(
        {"site": ask("Jira site URL (e.g. https://acme.atlassian.net)", "", _required)}
    )
